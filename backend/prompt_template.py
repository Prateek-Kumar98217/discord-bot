"""
prompt_template.py
──────────────────
Reference prompt template for CerebrusClient.

Budget guide (GPT-OSS-120B context window ≈ 128 k tokens)
──────────────────────────────────────────────────────────
Section                             Est. tokens
─────────────────────────────────── ──────────
System preamble                          ~120
Transcript (injected at runtime)      ~50–2 000
─── Output sections ───────────────────────────
  Summary                                ~120
  Key Topics                             ~200
  Action Items                           ~200
  Decisions Made                         ~120
  Open Questions / Follow-ups            ~120
  Sentiment & Tone (optional)             ~80
─────────────────────────────────── ──────────
Total (incl. transcript)          ~800–2 800
Remaining headroom for long calls    ~125 000+
"""

# ---------------------------------------------------------------------------
# System prompt — instructs the model on role, output format, and token
# targets so that responses stay predictable regardless of transcript length.
# ---------------------------------------------------------------------------
SYSTEM_PROMPT: str = """\
You are a highly accurate meeting-intelligence assistant embedded in a \
Discord voice-channel recording system.

Your job is to analyse a raw speech transcript and produce a structured \
report that is immediately useful to participants who were present and \
those who were not.

## Output format

Respond ONLY with valid JSON matching this schema (do NOT add markdown \
fences or any text outside the JSON):

{
  "summary":        "<string>",
  "key_topics":     ["<string>", ...],
  "action_items":   [{"owner": "<string|null>", "task": "<string>"}, ...],
  "decisions":      ["<string>", ...],
  "open_questions": ["<string>", ...],
  "sentiment":      "<positive|neutral|mixed|negative|unclear>"
}

## Section guidance & token targets

| Field           | Target length          | Notes                              |
|-----------------|------------------------|------------------------------------|
| summary         | 2–4 sentences (~120 t) | High-level TL;DR of the entire     |
|                 |                        | conversation.                      |
| key_topics      | 3–8 items (~200 t)     | Concise noun phrases (≤ 8 words    |
|                 |                        | each) representing the main themes.|
| action_items    | 0–N items (~200 t)     | Each item has an optional `owner`  |
|                 |                        | (person name or null) and a clear  |
|                 |                        | `task` description.                |
| decisions       | 0–N items (~120 t)     | Concrete conclusions reached.      |
|                 |                        | Only include firm decisions.       |
| open_questions  | 0–N items (~120 t)     | Unresolved questions or items that |
|                 |                        | need follow-up.                    |
| sentiment       | 1 label    (~80 t)     | Overall emotional tone of the      |
|                 |                        | conversation.                      |

## Rules

1. If a section has genuinely no content, use an empty array [] or an \
   appropriate null-equivalent value — do NOT fabricate items.
2. Do NOT include speaker diarisation IDs or raw timestamps unless they \
   appear verbatim in the transcript.
3. If the transcript is too short or unclear to fill a section, omit \
   items rather than guessing.
4. All text must be in the same language as the transcript unless the \
   user's metadata specifies otherwise.
"""

# ---------------------------------------------------------------------------
# User message template — {transcript} is replaced at runtime.
# Optional {metadata} slot accepts a JSON string of extra context
# (e.g. channel name, participant list, date/time).
# ---------------------------------------------------------------------------
USER_TEMPLATE: str = """\
{metadata_block}\
## Transcript

{transcript}

---
Analyse the transcript above and return the JSON report.\
"""

# ---------------------------------------------------------------------------
# Helper — build the filled-in user message
# ---------------------------------------------------------------------------


def build_user_message(
    transcript: str,
    metadata: dict | None = None,
) -> str:
    """
    Construct the user message string from the template.

    Parameters
    ----------
    transcript:
        Raw text produced by the Whisper transcription step.
    metadata:
        Optional dict of contextual information.  Recognised keys:

        - ``channel``   – Discord voice channel name
        - ``guild``     – Discord server (guild) name
        - ``user_id``   – Discord user ID who triggered recording
        - ``timestamp`` – ISO-8601 datetime string of the recording
        - ``duration_ms`` – clip duration in milliseconds

        Unknown keys are included as-is.

    Returns
    -------
    str
        The formatted user message ready to send to the model.
    """
    if metadata:
        lines = ["## Recording metadata\n"]
        label_map = {
            "channel": "Channel",
            "guild": "Server",
            "user_id": "User ID",
            "timestamp": "Recorded at",
            "duration_ms": "Duration (ms)",
        }
        for key, value in metadata.items():
            label = label_map.get(key, key.replace("_", " ").title())
            lines.append(f"- **{label}**: {value}")
        metadata_block = "\n".join(lines) + "\n\n"
    else:
        metadata_block = ""

    return USER_TEMPLATE.format(
        metadata_block=metadata_block,
        transcript=transcript.strip(),
    )
