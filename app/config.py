"""Settings storage. Everything the user can change from the Settings page lives here."""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("CLIPFORGE_DATA") or (BASE_DIR / "data")).resolve()
SETTINGS_FILE = DATA_DIR / "settings.json"
MASK = "********"

DEFAULTS: dict = {
    # --- LLM -----------------------------------------------------------
    "llm_provider": "ollama",  # "ollama" or "openai" (any OpenAI-compatible API)
    "ollama_host": "http://127.0.0.1:11434",
    "openai_base_url": "",
    "llm_api_key": "",
    "filter_model": "",  # pass 1: proposes moments in each transcript chunk
    "ranker_model": "",  # pass 2: ranks the shortlist
    "llm_temperature": 0.2,
    "llm_num_ctx": 16384,
    "llm_disable_thinking": True,
    "unload_models": True,  # free VRAM between stages
    # --- Transcription ---------------------------------------------------
    "whisper_model": "large-v3-turbo",
    "whisper_device": "auto",  # auto | cuda | cpu
    "whisper_compute": "auto",  # auto | float16 | int8_float16 | int8
    "language": "",  # empty = auto-detect
    # --- Clip selection --------------------------------------------------
    "min_clip_sec": 25,
    "max_clip_sec": 75,
    "num_candidates": 10,
    "chunk_minutes": 8,
    "energy_weight": 0.15,
    # --- Rendering -------------------------------------------------------
    "aspect": "9:16",  # 9:16 | 1:1 | 4:5 | 16:9
    "reframe": "face",  # face | center | blur
    "captions": True,
    "caption_font": "Arial",
    "caption_size": 84,
    "caption_color": "#FFFFFF",
    "caption_highlight": "#FFE600",
    "caption_outline": "#000000",
    "caption_words": 3,
    "caption_upper": True,
    "caption_position": 22,  # percent of height measured up from the bottom
    "encoder": "auto",  # auto | libx264 | h264_nvenc
    "quality": 21,  # lower = better (CRF)
    "output_width": 1080,
    # --- Download --------------------------------------------------------
    "max_height": 1080,
    "cookies": "",  # contents of a Netscape cookies.txt (helps when YouTube blocks servers)
    # --- Storage ---------------------------------------------------------
    "clips_dir": "",  # empty = <data>/clips
    # --- YouTube upload --------------------------------------------------
    "yt_client_id": "",
    "yt_client_secret": "",
    "yt_privacy": "private",
    "yt_add_shorts_tag": True,
}

SECRET_KEYS = {"llm_api_key", "cookies", "yt_client_secret"}

_lock = threading.RLock()
_cache: dict | None = None


def ensure_dirs() -> None:
    for sub in ("", "videos", "models", "models/whisper", "fonts"):
        (DATA_DIR / sub).mkdir(parents=True, exist_ok=True)
    clips_dir().mkdir(parents=True, exist_ok=True)


def _load() -> dict:
    global _cache
    if _cache is None:
        data = dict(DEFAULTS)
        if SETTINGS_FILE.exists():
            try:
                saved = json.loads(SETTINGS_FILE.read_text("utf-8"))
                for k, v in saved.items():
                    if k in DEFAULTS:
                        data[k] = _coerce(k, v)
            except Exception:
                pass
        _cache = data
    return _cache


def _coerce(key: str, value):
    default = DEFAULTS[key]
    try:
        if isinstance(default, bool):
            if isinstance(value, str):
                return value.strip().lower() in ("1", "true", "yes", "on")
            return bool(value)
        if isinstance(default, int):
            return int(float(value))
        if isinstance(default, float):
            return float(value)
        return "" if value is None else str(value)
    except (TypeError, ValueError):
        return default


def get() -> dict:
    with _lock:
        return dict(_load())


def update(patch: dict) -> dict:
    with _lock:
        data = _load()
        for k, v in patch.items():
            if k not in DEFAULTS:
                continue
            if k in SECRET_KEYS and (v is None or v == MASK):
                continue  # untouched secret field
            data[k] = _coerce(k, v)
        # sanity rules
        data["min_clip_sec"] = max(5, data["min_clip_sec"])
        if data["max_clip_sec"] < data["min_clip_sec"] + 5:
            data["max_clip_sec"] = data["min_clip_sec"] + 5
        data["num_candidates"] = min(40, max(1, data["num_candidates"]))
        data["chunk_minutes"] = min(30, max(2, data["chunk_minutes"]))
        data["caption_words"] = min(8, max(1, data["caption_words"]))
        data["energy_weight"] = min(0.5, max(0.0, data["energy_weight"]))
        data["ollama_host"] = data["ollama_host"].strip().rstrip("/") or DEFAULTS["ollama_host"]
        data["openai_base_url"] = data["openai_base_url"].strip().rstrip("/")
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = SETTINGS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), "utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        tmp.replace(SETTINGS_FILE)
        return dict(data)


def public() -> dict:
    """Settings safe to send to the browser: secrets are replaced by a '<key>_set' flag."""
    data = get()
    out = {}
    for k, v in data.items():
        if k in SECRET_KEYS:
            out[k] = ""
            out[k + "_set"] = bool(v)
        else:
            out[k] = v
    return out


def clips_dir() -> Path:
    c = _load().get("clips_dir", "").strip()
    return Path(c).expanduser() if c else DATA_DIR / "clips"
