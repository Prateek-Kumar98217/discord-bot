from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from datetime import datetime
from fastapi.responses import Response
from datetime import datetime

app = FastAPI()
audio_store: dict[str:bytes] = {}


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
    audio_store[audio.filename] = wav_bytes

    print(
        {
            "received": True,
            "filename": audio.filename,
            "size_bytes": len(wav_bytes),
            "userId": userId,
        }
    )

    return {
        "received": True,
        "filename": audio.filename,
        "size_bytes": len(wav_bytes),
        "userId": userId,
    }


@app.get("/")
async def read_root():
    return {"Hello": "World"}


@app.get("/audio/{file_id}")
async def get_audio_from_server(file_id: str):
    if file_id not in audio_store:
        raise HTTPException(status_code=404, detail="Audio not found")

    return Response(
        content=audio_store[file_id],
        media_type="audio/wav",
        headers={"Content-Disposition": f'attachment; filename="{file_id}"'},
    )
