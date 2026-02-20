"""
GroqClient — wraps the Groq SDK to provide auto-rotating API-key and
Whisper-model selection with transparent retry logic.

Lifecycle
---------
Call ``groq_client.init()`` once at server startup (inside FastAPI lifespan).
Call ``groq_client.close()`` once at server shutdown.
Then call ``await groq_client.transcribe(audio_bytes, filename)`` from any
request handler.
"""

from __future__ import annotations

import io
import itertools
import logging
import os
from typing import Iterator

from groq import AsyncGroq, APIConnectionError, APIStatusError, RateLimitError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default Whisper models available on Groq (fastest → most accurate)
# Override via GROQ_WHISPER_MODELS env var.
# ---------------------------------------------------------------------------
DEFAULT_WHISPER_MODELS: list[str] = [
    "whisper-large-v3-turbo",  # best speed / quality balance
    "whisper-large-v3",  # highest accuracy, slower
]


class GroqClient:
    """
    Singleton-style client that manages a pool of Groq API keys and a list
    of Whisper model names.  Both are cycled round-robin; on any
    rate-limit / API error the next key+model pair is tried automatically.
    """

    def __init__(self) -> None:
        self._clients: list[AsyncGroq] = []
        self._models: list[str] = []
        self._client_iter: Iterator[AsyncGroq] | None = None
        self._model_iter: Iterator[str] | None = None

    # ------------------------------------------------------------------
    # Lifecycle helpers
    # ------------------------------------------------------------------

    def init(self) -> None:
        """
        Load API keys and model names from environment variables, then build
        the pool of ``AsyncGroq`` clients.

        Environment variables
        ~~~~~~~~~~~~~~~~~~~~~
        GROQ_API_KEY        – single Groq API key (optional if GROQ_API_KEYS set)
        GROQ_API_KEYS       – comma-separated list of Groq API keys (optional)
        GROQ_WHISPER_MODELS – comma-separated list of Whisper model IDs to use
                              (defaults to DEFAULT_WHISPER_MODELS)

        At least one key must be present.
        """
        api_keys = self._load_api_keys()
        if not api_keys:
            raise RuntimeError(
                "No Groq API keys found. "
                "Set GROQ_API_KEY and/or GROQ_API_KEYS in your environment."
            )

        self._clients = [AsyncGroq(api_key=key) for key in api_keys]
        self._client_iter = itertools.cycle(self._clients)

        self._models = self._load_models()
        self._model_iter = itertools.cycle(self._models)

        logger.info(
            "[GroqClient] Initialised — %d key(s), %d model(s): %s",
            len(self._clients),
            len(self._models),
            self._models,
        )

    def close(self) -> None:
        """Release all resources and reset internal state."""
        self._clients.clear()
        self._models.clear()
        self._client_iter = None
        self._model_iter = None
        logger.info("[GroqClient] Shut down cleanly.")

    # ------------------------------------------------------------------
    # Key / model rotation
    # ------------------------------------------------------------------

    @staticmethod
    def _load_api_keys() -> list[str]:
        """
        Collect unique, non-empty API keys from:
          1. ``GROQ_API_KEY``  (single key)
          2. ``GROQ_API_KEYS`` (comma-separated list)
        Duplicates are removed while preserving order.
        """
        seen: set[str] = set()
        keys: list[str] = []

        for raw in [
            os.getenv("GROQ_API_KEY", ""),
            *os.getenv("GROQ_API_KEYS", "").split(","),
        ]:
            key = raw.strip()
            if key and key not in seen:
                seen.add(key)
                keys.append(key)

        return keys

    @staticmethod
    def _load_models() -> list[str]:
        """
        Return model list from ``GROQ_WHISPER_MODELS`` env var, or fall back
        to ``DEFAULT_WHISPER_MODELS``.
        """
        raw = os.getenv("GROQ_WHISPER_MODELS", "").strip()
        if raw:
            models = [m.strip() for m in raw.split(",") if m.strip()]
            if models:
                return models
        return list(DEFAULT_WHISPER_MODELS)

    def _next_client(self) -> AsyncGroq:
        if self._client_iter is None:
            raise RuntimeError(
                "GroqClient has not been initialised. Call init() first."
            )
        return next(self._client_iter)

    def _next_model(self) -> str:
        if self._model_iter is None:
            raise RuntimeError(
                "GroqClient has not been initialised. Call init() first."
            )
        return next(self._model_iter)

    # ------------------------------------------------------------------
    # Transcription
    # ------------------------------------------------------------------

    async def transcribe(
        self,
        audio_bytes: bytes,
        filename: str = "audio.wav",
        language: str | None = None,
    ) -> str:
        """
        Transcribe *audio_bytes* (WAV/MP3/…) using the Groq Whisper API.

        The method rotates through every (client, model) pair before giving up,
        so transient rate-limit errors on one key or model are handled silently.

        Parameters
        ----------
        audio_bytes:
            Raw audio data.
        filename:
            Hint used by Groq to detect the audio format (e.g. ``"clip.wav"``).
        language:
            BCP-47 language code (e.g. ``"en"``).  ``None`` lets Whisper
            auto-detect.

        Returns
        -------
        str
            The transcript text.

        Raises
        ------
        RuntimeError
            When all key+model combinations have been exhausted without success.
        """
        if self._client_iter is None or self._model_iter is None:
            raise RuntimeError(
                "GroqClient has not been initialised. Call init() first."
            )

        # Maximum attempts = keys × models (full rotation of both dimensions)
        max_attempts = max(len(self._clients), len(self._models)) * 2
        last_error: Exception | None = None

        for attempt in range(1, max_attempts + 1):
            client = self._next_client()
            model = self._next_model()

            logger.debug(
                "[GroqClient] transcribe attempt %d/%d — model=%s",
                attempt,
                max_attempts,
                model,
            )

            try:
                response = await client.audio.transcriptions.create(
                    file=(filename, io.BytesIO(audio_bytes), "audio/wav"),
                    model=model,
                    language=language,
                    response_format="text",
                )
                # response_format="text" returns a plain string
                transcript: str = (
                    response if isinstance(response, str) else response.text
                )
                logger.info(
                    "[GroqClient] Transcription successful — model=%s, chars=%d",
                    model,
                    len(transcript),
                )
                return transcript

            except RateLimitError as exc:
                logger.warning(
                    "[GroqClient] Rate limit on model=%s (attempt %d): %s — rotating…",
                    model,
                    attempt,
                    exc,
                )
                last_error = exc

            except APIStatusError as exc:
                logger.warning(
                    "[GroqClient] API status error on model=%s (attempt %d): %s — rotating…",
                    model,
                    attempt,
                    exc,
                )
                last_error = exc

            except APIConnectionError as exc:
                logger.warning(
                    "[GroqClient] Connection error on model=%s (attempt %d): %s — rotating…",
                    model,
                    attempt,
                    exc,
                )
                last_error = exc

        raise RuntimeError(
            f"All {max_attempts} transcription attempt(s) failed. "
            f"Last error: {last_error}"
        )


# ---------------------------------------------------------------------------
# Module-level singleton — import and use this everywhere
# ---------------------------------------------------------------------------
groq_client = GroqClient()
