"""Model management: Ollama models, Whisper models, and a system check."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time

import requests

from . import config

# --------------------------------------------------------------------------
# Whisper
# --------------------------------------------------------------------------
WHISPER_MODELS = {
    "large-v3-turbo": {"size_mb": 1600, "note": "Fast and accurate. Best default for a GPU.", "multilingual": True},
    "large-v3": {"size_mb": 3100, "note": "Most accurate. Slower and needs about 5 GB of VRAM.", "multilingual": True},
    "distil-large-v3": {"size_mb": 1500, "note": "Very fast. English only.", "multilingual": False},
    "medium": {"size_mb": 1500, "note": "Balanced. Fine on CPU for short videos.", "multilingual": True},
    "small": {"size_mb": 480, "note": "Light. Good for testing.", "multilingual": True},
    "base": {"size_mb": 145, "note": "Very light. Lower accuracy.", "multilingual": True},
    "tiny": {"size_mb": 75, "note": "Smallest. For smoke tests only.", "multilingual": True},
}

_state_lock = threading.Lock()
_ollama_pulls: dict[str, dict] = {}
_whisper_dl: dict[str, dict] = {}


def whisper_dir(name: str):
    return config.DATA_DIR / "models" / "whisper" / name


def whisper_downloaded(name: str) -> bool:
    return (whisper_dir(name) / "model.bin").exists()


def whisper_list() -> list[dict]:
    out = []
    for name, meta in WHISPER_MODELS.items():
        with _state_lock:
            dl = dict(_whisper_dl.get(name, {}))
        out.append({
            "name": name, **meta,
            "downloaded": whisper_downloaded(name),
            "download": dl or None,
        })
    return out


def _dir_size(path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def whisper_download(name: str) -> None:
    if name not in WHISPER_MODELS:
        raise ValueError(f"Unknown Whisper model: {name}")
    with _state_lock:
        cur = _whisper_dl.get(name)
        if cur and not cur.get("done") and not cur.get("error"):
            return  # already running
        _whisper_dl[name] = {"done": False, "error": None, "percent": 0.0, "status": "Starting download"}

    def run():
        target = whisper_dir(name)
        expected = WHISPER_MODELS[name]["size_mb"] * 1024 * 1024
        stop = threading.Event()

        def watcher():
            while not stop.is_set():
                pct = min(99.0, _dir_size(target) / expected * 100) if target.exists() else 0.0
                with _state_lock:
                    _whisper_dl[name].update(percent=round(pct, 1), status="Downloading")
                time.sleep(0.7)

        t = threading.Thread(target=watcher, daemon=True)
        t.start()
        try:
            from faster_whisper.utils import download_model
            target.mkdir(parents=True, exist_ok=True)
            download_model(name, output_dir=str(target))
            if not whisper_downloaded(name):
                raise RuntimeError("Download finished but model.bin is missing")
            with _state_lock:
                _whisper_dl[name].update(done=True, percent=100.0, status="Ready")
        except Exception as e:  # noqa: BLE001
            with _state_lock:
                _whisper_dl[name].update(error=str(e)[:500], status="Failed")
        finally:
            stop.set()

    threading.Thread(target=run, daemon=True).start()


def whisper_delete(name: str) -> None:
    shutil.rmtree(whisper_dir(name), ignore_errors=True)
    with _state_lock:
        _whisper_dl.pop(name, None)


# --------------------------------------------------------------------------
# Ollama
# --------------------------------------------------------------------------
def _host() -> str:
    return config.get()["ollama_host"]


def ollama_version() -> str | None:
    try:
        r = requests.get(_host() + "/api/version", timeout=3)
        return r.json().get("version")
    except Exception:  # noqa: BLE001
        return None


def ollama_list() -> list[dict]:
    r = requests.get(_host() + "/api/tags", timeout=8)
    r.raise_for_status()
    out = []
    for m in r.json().get("models", []):
        out.append({
            "name": m.get("name"),
            "size": m.get("size", 0),
            "modified": m.get("modified_at"),
            "params": (m.get("details") or {}).get("parameter_size"),
            "quant": (m.get("details") or {}).get("quantization_level"),
        })
    return out


def ollama_pull(name: str) -> None:
    name = name.strip()
    if not name:
        raise ValueError("Model name is empty")
    with _state_lock:
        cur = _ollama_pulls.get(name)
        if cur and not cur.get("done") and not cur.get("error"):
            return
        _ollama_pulls[name] = {"done": False, "error": None, "percent": 0.0, "status": "Starting", "layers": {}}

    def run():
        try:
            with requests.post(_host() + "/api/pull", json={"model": name, "stream": True},
                               stream=True, timeout=(10, 3600)) as r:
                if r.status_code != 200:
                    raise RuntimeError(r.text[:300] or f"Ollama returned {r.status_code}")
                for line in r.iter_lines():
                    if not line:
                        continue
                    ev = json.loads(line)
                    if ev.get("error"):
                        raise RuntimeError(ev["error"])
                    with _state_lock:
                        st = _ollama_pulls[name]
                        st["status"] = ev.get("status", st["status"])
                        dig = ev.get("digest")
                        if dig and ev.get("total"):
                            st["layers"][dig] = (ev["total"], ev.get("completed", 0))
                        total = sum(t for t, _ in st["layers"].values())
                        done = sum(c for _, c in st["layers"].values())
                        if total:
                            st["percent"] = round(min(99.0, done / total * 100), 1)
                            st["total"] = total
                            st["completed"] = done
            with _state_lock:
                _ollama_pulls[name].update(done=True, percent=100.0, status="Ready")
        except Exception as e:  # noqa: BLE001
            with _state_lock:
                _ollama_pulls[name].update(error=str(e)[:500], status="Failed")

    threading.Thread(target=run, daemon=True).start()


def ollama_delete(name: str) -> None:
    r = requests.delete(_host() + "/api/delete", json={"model": name}, timeout=30)
    if r.status_code >= 400:
        raise RuntimeError(r.text[:300])
    with _state_lock:
        _ollama_pulls.pop(name, None)


def ollama_unload(model: str) -> None:
    """Ask Ollama to free the model from VRAM (best effort)."""
    try:
        requests.post(_host() + "/api/generate", json={"model": model, "keep_alive": 0}, timeout=15)
    except Exception:  # noqa: BLE001
        pass


def pulls_status() -> dict:
    with _state_lock:
        ollama = {k: {kk: vv for kk, vv in v.items() if kk != "layers"} for k, v in _ollama_pulls.items()}
        whisper = {k: dict(v) for k, v in _whisper_dl.items()}
    return {"ollama": ollama, "whisper": whisper}


# --------------------------------------------------------------------------
# System info
# --------------------------------------------------------------------------
def gpu_info() -> list[dict]:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return []
    try:
        out = subprocess.run(
            [exe, "--query-gpu=name,memory.total,memory.used", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        ).stdout
        gpus = []
        for line in out.strip().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 3:
                gpus.append({"name": parts[0], "vram_total_mb": int(parts[1]), "vram_used_mb": int(parts[2])})
        return gpus
    except Exception:  # noqa: BLE001
        return []


def ytdlp_version() -> str | None:
    try:
        import yt_dlp
        return yt_dlp.version.__version__
    except Exception:  # noqa: BLE001
        return None


def faster_whisper_version() -> str | None:
    try:
        import faster_whisper
        return faster_whisper.__version__
    except Exception:  # noqa: BLE001
        return None
