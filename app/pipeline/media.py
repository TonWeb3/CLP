"""ffmpeg / ffprobe helpers shared by the pipeline."""
from __future__ import annotations

import json
import shutil
import subprocess
import wave
from pathlib import Path

FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = shutil.which("ffprobe") or "ffprobe"


def have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def probe(path: str | Path) -> dict:
    out = subprocess.run(
        [FFPROBE, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, timeout=60,
    )
    if out.returncode != 0:
        raise RuntimeError("ffprobe failed: " + out.stderr[-300:])
    info = json.loads(out.stdout)
    video = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
    audio = next((s for s in info.get("streams", []) if s.get("codec_type") == "audio"), None)
    dur = float((info.get("format") or {}).get("duration") or (video or {}).get("duration") or 0)
    return {
        "duration": dur,
        "width": int(video["width"]) if video else 0,
        "height": int(video["height"]) if video else 0,
        "has_audio": audio is not None,
        "has_video": video is not None,
    }


def extract_audio(src: str | Path, dst: str | Path) -> None:
    """16 kHz mono PCM wav, the format Whisper wants."""
    r = subprocess.run(
        [FFMPEG, "-y", "-v", "error", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000",
         "-c:a", "pcm_s16le", str(dst)],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise RuntimeError("Could not extract audio: " + r.stderr[-300:])


def make_thumb(src: str | Path, dst: str | Path, at: float = 1.0, width: int = 480) -> bool:
    r = subprocess.run(
        [FFMPEG, "-y", "-v", "error", "-ss", f"{max(at, 0):.2f}", "-i", str(src), "-frames:v", "1",
         "-vf", f"scale={width}:-2", str(dst)],
        capture_output=True, text=True,
    )
    return r.returncode == 0 and Path(dst).exists()


_encoder_cache: dict[str, str] = {}


def pick_encoder(preference: str = "auto") -> str:
    """Return 'libx264' or 'h264_nvenc'. 'auto' test-encodes with NVENC once."""
    if preference in ("libx264", "h264_nvenc"):
        return preference
    if "auto" in _encoder_cache:
        return _encoder_cache["auto"]
    enc = "libx264"
    try:
        listed = subprocess.run([FFMPEG, "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=15).stdout
        if "h264_nvenc" in listed:
            t = subprocess.run(
                [FFMPEG, "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=256x256:d=0.2",
                 "-c:v", "h264_nvenc", "-f", "null", "-"],
                capture_output=True, text=True, timeout=30,
            )
            if t.returncode == 0:
                enc = "h264_nvenc"
    except Exception:  # noqa: BLE001
        pass
    _encoder_cache["auto"] = enc
    return enc


def energy_curve(wav_path: str | Path, step: float = 0.5) -> list[float]:
    """RMS loudness per `step` seconds from a 16-bit PCM wav (numpy only)."""
    import numpy as np

    out: list[float] = []
    with wave.open(str(wav_path), "rb") as w:
        rate = w.getframerate()
        width = w.getsampwidth()
        if width != 2:
            return []
        per = max(1, int(rate * step))
        while True:
            raw = w.readframes(per * 600)  # ~5 min blocks
            if not raw:
                break
            arr = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            n = len(arr) // per
            if n == 0:
                if len(arr):
                    out.append(float(np.sqrt(np.mean(arr ** 2))))
                break
            blocks = arr[: n * per].reshape(n, per)
            out.extend(np.sqrt(np.mean(blocks ** 2, axis=1)).tolist())
    return out
