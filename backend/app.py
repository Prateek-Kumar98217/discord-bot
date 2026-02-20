import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.responses import Response

from groq_client import groq_client
from cerebrus_client import cerebrus_client

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

load_dotenv()

# ---------------------------------------------------------------------------
# In-memory stores
# ---------------------------------------------------------------------------
audio_store: dict[str, bytes] = {}
transcript_store: dict[str, str] = {}
llm_store: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# FastAPI lifespan — init / close GroqClient around server lifetime
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("[Server] Starting up — initialising clients…")
    groq_client.init()
    cerebrus_client.init()
    yield
    logger.info("[Server] Shutting down — closing clients…")
    groq_client.close()
    cerebrus_client.close()


app = FastAPI(lifespan=lifespan)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/")
async def read_root():
    return {"status": "ok"}


# ── Audio ingest ────────────────────────────────────────────────────────────


@app.post("/audio")
async def receive_audio(
    audio: UploadFile = File(...),
    userId: str = Form(...),
    guildId: str = Form(...),
    durationMs: int = Form(...),
    sampleRate: int = Form(...),
    channels: int = Form(...),
    timestamp: datetime = Form(...),
):
    wav_bytes = await audio.read()
    file_id = audio.filename
    audio_store[file_id] = wav_bytes

    logger.info(
        "[Audio] Received %s — user=%s guild=%s size=%d bytes",
        file_id,
        userId,
        guildId,
        len(wav_bytes),
    )

    # ── Transcription ──────────────────────────────────────────────────────
    transcript: str | None = None
    try:
        transcript = await groq_client.transcribe(wav_bytes, filename=file_id)
        transcript_store[file_id] = transcript
        logger.info("[Transcription] Stored transcript for %s", file_id)
    except Exception as exc:
        logger.error("[Transcription] Failed for %s: %s", file_id, exc)

    # ── LLM analysis ───────────────────────────────────────────────────────
    llm_result: dict | None = None
    if transcript:
        try:
            meta = {
                "user_id": userId,
                "guild": guildId,
                "timestamp": timestamp.isoformat(),
                "duration_ms": durationMs,
            }
            llm_result = await cerebrus_client.process(transcript, metadata=meta)
            llm_store[file_id] = llm_result
            logger.info("[LLM] Stored analysis for %s", file_id)
        except Exception as exc:
            logger.error("[LLM] Analysis failed for %s: %s", file_id, exc)

    return {
        "received": True,
        "filename": file_id,
        "size_bytes": len(wav_bytes),
        "userId": userId,
        "guildId": guildId,
        "transcript": transcript,
        "analysis": llm_result,
    }


# ── Audio retrieval ─────────────────────────────────────────────────────────


@app.get("/audio/{file_id}")
async def get_audio(file_id: str):
    if file_id not in audio_store:
        raise HTTPException(status_code=404, detail="Audio not found")

    return Response(
        content=audio_store[file_id],
        media_type="audio/wav",
        headers={"Content-Disposition": f'attachment; filename="{file_id}"'},
    )


# ── Transcript retrieval ─────────────────────────────────────────────────────


@app.get("/transcriptions")
async def list_transcriptions():
    """Return a summary list of all stored transcriptions."""
    return [
        {"file_id": fid, "transcript": text} for fid, text in transcript_store.items()
    ]


@app.get("/transcriptions/{file_id}")
async def get_transcription(file_id: str):
    """Retrieve the transcript for a specific audio file."""
    if file_id not in transcript_store:
        raise HTTPException(status_code=404, detail="Transcript not found")

    return {"file_id": file_id, "transcript": transcript_store[file_id]}


# ── LLM analysis retrieval ───────────────────────────────────────────────────


@app.get("/analysis")
async def list_analyses():
    """Return a list of all stored LLM analyses."""
    return [{"file_id": fid, "analysis": result} for fid, result in llm_store.items()]


@app.get("/analysis/{file_id}")
async def get_analysis(file_id: str):
    """Retrieve the LLM analysis for a specific audio file."""
    if file_id not in llm_store:
        raise HTTPException(status_code=404, detail="Analysis not found")

    return {"file_id": file_id, "analysis": llm_store[file_id]}
