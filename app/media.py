from __future__ import annotations

import io
import shutil
import struct
import subprocess
import wave
from pathlib import Path
from typing import Any

from app.hashing import sha256_bytes
from app.models import ScriptArtifact


def make_wav(duration: int) -> bytes:
    rate, count = 8000, duration * 8000
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(b"".join(struct.pack("<h", 700 if (i // 80) % 2 else -700) for i in range(count)))
    return output.getvalue()


def srt_time(seconds: float) -> str:
    ms = round(seconds * 1000)
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def captions(script: ScriptArtifact) -> str:
    words = script.caption_text.split()
    chunks = [" ".join(words[i:i + 8]) for i in range(0, len(words), 8)]
    duration = script.estimated_duration_seconds
    rows = []
    for i, text in enumerate(chunks):
        start = duration * i / len(chunks)
        end = duration * (i + 1) / len(chunks)
        rows.append(f"{i + 1}\n{srt_time(start)} --> {srt_time(end)}\n{text}")
    return "\n\n".join(rows) + "\n"


def render(root: Path, renderer: str, fail_once: bool, audio: bytes, caption_text: str) -> dict[str, Any]:
    if fail_once:
        marker = root / ".render-failed-once"
        if not marker.exists():
            marker.write_bytes(b"failed")
            raise RuntimeError("simulated render failure")
    if renderer == "mock":
        return {"renderer": "mock", "label": "synthetic placeholder", "audio_sha256": sha256_bytes(audio),
                "caption_sha256": sha256_bytes(caption_text.encode()), "container": "json-placeholder"}
    if renderer != "ffmpeg":
        raise ValueError("renderer must be mock or ffmpeg")
    executable = shutil.which("ffmpeg")
    if not executable:
        raise RuntimeError("FFmpeg unavailable: install ffmpeg or select --renderer mock")
    wav_path, mp4_path = root / "audio.wav", root / "final.mp4"
    proc = subprocess.run([executable, "-y", "-f", "lavfi", "-i", "color=c=0x182433:s=640x360:r=24",
        "-i", str(wav_path), "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", str(mp4_path)], capture_output=True, text=True, check=False)
    if proc.returncode or not mp4_path.is_file():
        raise RuntimeError(f"FFmpeg exit={proc.returncode}: {proc.stderr[-300:]}")
    payload = mp4_path.read_bytes()
    return {"renderer": "ffmpeg", "path": "final.mp4", "sha256": sha256_bytes(payload),
            "exit_code": proc.returncode, "stdout": proc.stdout[-300:], "stderr": proc.stderr[-300:]}
