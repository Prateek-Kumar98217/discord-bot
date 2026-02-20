"""
CerebrusClient — wraps the Cerebras Cloud SDK to process transcripts through
GPT-OSS-120B with automatic API-key rotation and retry logic.

Lifecycle
---------
Call ``cerebrus_client.init()`` once at server startup (inside FastAPI lifespan).
Call ``cerebrus_client.close()`` once at server shutdown.
Then call ``await cerebrus_client.process(transcript, metadata)`` from any
request handler.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
import os
from typing import Iterator

from cerebras.cloud.sdk import (
    AsyncCerebras,
    APIConnectionError,
    APIStatusError,
    RateLimitError,
)

from prompt_template import SYSTEM_PROMPT, build_user_message

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Model default — override via CEREBRAS_MODEL env var
# ---------------------------------------------------------------------------
DEFAULT_MODEL: str = "gpt-oss-120b"


class CerebrusClient:
    """
    Singleton-style client for the Cerebras Cloud API.

    Manages a pool of ``AsyncCerebras`` clients (one per API key) and rotates
    through them round-robin.  On any rate-limit or API error the next key is
    tried automatically before the call is considered failed.
    """

    def __init__(self) -> None:
        self._clients: list[AsyncCerebras] = []
        self._client_iter: Iterator[AsyncCerebras] | None = None
        self._model: str = DEFAULT_MODEL

    # ------------------------------------------------------------------
    # Lifecycle helpers
    # ------------------------------------------------------------------

    def init(self) -> None:
        """
        Load API keys and configuration from environment variables, then build
        the pool of ``AsyncCerebras`` clients.

        Environment variables
        ~~~~~~~~~~~~~~~~~~~~~
        CEREBRAS_API_KEY    – single Cerebras API key
        CEREBRAS_API_KEYS   – comma-separated list of Cerebras API keys
        CEREBRAS_MODEL      – LLM model ID to use (default: gpt-oss-120b)

        At least one API key must be present.
        """
        self._model = os.getenv("CEREBRAS_MODEL", DEFAULT_MODEL).strip()

        api_keys = self._load_api_keys()
        if not api_keys:
            raise RuntimeError(
                "No Cerebras API keys found. "
                "Set CEREBRAS_API_KEY and/or CEREBRAS_API_KEYS in your environment."
            )

        self._clients = [AsyncCerebras(api_key=key) for key in api_keys]
        self._client_iter = itertools.cycle(self._clients)

        logger.info(
            "[CerebrusClient] Initialised — %d key(s), model=%s",
            len(self._clients),
            self._model,
        )

    def close(self) -> None:
        """Release all resources and reset internal state."""
        self._clients.clear()
        self._client_iter = None
        logger.info("[CerebrusClient] Shut down cleanly.")

    # ------------------------------------------------------------------
    # Key rotation
    # ------------------------------------------------------------------

    @staticmethod
    def _load_api_keys() -> list[str]:
        """
        Collect unique, non-empty keys from:
          1. ``CEREBRAS_API_KEY``  (single key)
          2. ``CEREBRAS_API_KEYS`` (comma-separated list)
        Duplicates are removed while preserving insertion order.
        """
        seen: set[str] = set()
        keys: list[str] = []

        for raw in [
            os.getenv("CEREBRAS_API_KEY", ""),
            *os.getenv("CEREBRAS_API_KEYS", "").split(","),
        ]:
            key = raw.strip()
            if key and key not in seen:
                seen.add(key)
                keys.append(key)

        return keys

    def _next_client(self) -> AsyncCerebras:
        if self._client_iter is None:
            raise RuntimeError(
                "CerebrusClient has not been initialised. Call init() first."
            )
        return next(self._client_iter)

    # ------------------------------------------------------------------
    # LLM processing
    # ------------------------------------------------------------------

    async def process(
        self,
        transcript: str,
        metadata: dict | None = None,
        temperature: float = 0.2,
        max_completion_tokens: int = 1024,
    ) -> dict:
        """
        Send *transcript* through the Cerebras GPT-OSS-120B model and return a
        structured analysis dict.

        The system prompt and user message are built from ``prompt_template.py``.
        Keys rotate automatically on rate-limit or API errors.

        Parameters
        ----------
        transcript:
            Raw text from the Whisper transcription step.
        metadata:
            Optional dict with contextual info (channel, guild, user_id,
            timestamp, duration_ms).  Passed verbatim into the user message.
        temperature:
            Sampling temperature.  Lower = more deterministic.  Default 0.2
            keeps JSON output consistent.
        max_completion_tokens:
            Maximum tokens for the model response.  Default 1024 covers all
            output sections with room to spare.

        Returns
        -------
        dict
            Parsed JSON object produced by the model, with keys:
            ``summary``, ``key_topics``, ``action_items``, ``decisions``,
            ``open_questions``, ``sentiment``.

        Raises
        ------
        RuntimeError
            When all keys have been exhausted without a successful response.
        ValueError
            When the model returns a response that cannot be parsed as JSON.
        """
        if self._client_iter is None:
            raise RuntimeError(
                "CerebrusClient has not been initialised. Call init() first."
            )

        user_message = build_user_message(transcript, metadata)
        max_attempts = len(self._clients) * 2
        last_error: Exception | None = None

        for attempt in range(1, max_attempts + 1):
            client = self._next_client()

            logger.debug(
                "[CerebrusClient] process attempt %d/%d — model=%s",
                attempt,
                max_attempts,
                self._model,
            )

            try:
                response = await client.chat.completions.create(
                    model=self._model,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_message},
                    ],
                    temperature=temperature,
                    max_completion_tokens=max_completion_tokens,
                    top_p=1,
                    stream=False,
                )

                raw_text: str = response.choices[0].message.content or ""

                try:
                    result: dict = json.loads(raw_text)
                except json.JSONDecodeError as parse_err:
                    logger.error(
                        "[CerebrusClient] JSON parse error on attempt %d: %s\nRaw: %.300s",
                        attempt,
                        parse_err,
                        raw_text,
                    )
                    raise ValueError(
                        f"Model returned non-JSON response: {raw_text[:200]}"
                    ) from parse_err

                logger.info(
                    "[CerebrusClient] Process successful — model=%s, "
                    "topics=%d, actions=%d",
                    self._model,
                    len(result.get("key_topics", [])),
                    len(result.get("action_items", [])),
                )
                return result

            except RateLimitError as exc:
                logger.warning(
                    "[CerebrusClient] Rate limit (attempt %d): %s — rotating key…",
                    attempt,
                    exc,
                )
                last_error = exc

            except APIStatusError as exc:
                logger.warning(
                    "[CerebrusClient] API status error (attempt %d): %s — rotating key…",
                    attempt,
                    exc,
                )
                last_error = exc

            except APIConnectionError as exc:
                logger.warning(
                    "[CerebrusClient] Connection error (attempt %d): %s — rotating key…",
                    attempt,
                    exc,
                )
                last_error = exc

        raise RuntimeError(
            f"All {max_attempts} Cerebras attempt(s) failed. "
            f"Last error: {last_error}"
        )


# ---------------------------------------------------------------------------
# Module-level singleton — import and use this everywhere
# ---------------------------------------------------------------------------
cerebrus_client = CerebrusClient()
