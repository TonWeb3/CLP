"""Speech to text with faster-whisper, plus sentence building."""
from __future__ import annotations

import gc

from .. import models_mgr

SENT_END = (".", "?", "!", "…", "。", "？", "！", "؟")


def resolve_device(cfg: dict) -> tuple[str, str]:
    device = cfg.get("whisper_device", "auto")
    compute = cfg.get("whisper_compute", "auto")
    if device == "auto":
        try:
            import ctranslate2
            device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        except Exception:  # noqa: BLE001
            device = "cpu"
    if compute == "auto":
        compute = "float16" if device == "cuda" else "int8"
    return device, compute


def _run(model_path: str, audio_path: str, device: str, compute: str, cfg: dict, duration: float, progress_cb):
    from faster_whisper import WhisperModel

    model = WhisperModel(model_path, device=device, compute_type=compute)
    try:
        segments, info = model.transcribe(
            audio_path,
            word_timestamps=True,
            vad_filter=True,
            language=(cfg.get("language") or None),
            beam_size=5 if device == "cuda" else 1,
            condition_on_previous_text=False,
        )
        words: list[dict] = []
        for seg in segments:
            for w in (seg.words or []):
                t = (w.word or "").strip()
                if t:
                    words.append({"w": t, "s": round(float(w.start), 3), "e": round(float(w.end), 3)})
            if duration:
                progress_cb(min(seg.end / duration, 0.99), "Transcribing speech")
        return words, info.language
    finally:
        del model
        gc.collect()


def transcribe(audio_path: str, cfg: dict, duration: float, progress_cb) -> tuple[list[dict], str]:
    name = cfg.get("whisper_model", "large-v3-turbo")
    if not models_mgr.whisper_downloaded(name):
        raise RuntimeError(
            f"The Whisper model '{name}' is not downloaded yet. Open the Models page and download it first.")
    model_path = str(models_mgr.whisper_dir(name))
    device, compute = resolve_device(cfg)
    try:
        return _run(model_path, audio_path, device, compute, cfg, duration, progress_cb)
    except Exception as e:  # noqa: BLE001
        msg = str(e).lower()
        gpu_problem = device == "cuda" and any(k in msg for k in ("cudnn", "cublas", "cuda", "libcu"))
        if not gpu_problem:
            raise
        progress_cb(0.0, "GPU libraries missing, falling back to CPU (slow)")
        return _run(model_path, audio_path, "cpu", "int8", cfg, duration, progress_cb)


def build_sentences(words: list[dict]) -> list[dict]:
    """Group words into sentences. Each sentence knows its word index range."""
    sentences: list[dict] = []
    start_idx = 0
    n = len(words)
    for i, w in enumerate(words):
        gap = (words[i + 1]["s"] - w["e"]) if i + 1 < n else 99.0
        ended = w["w"].endswith(SENT_END)
        too_long = (w["e"] - words[start_idx]["s"]) > 20 or (i - start_idx) >= 55
        if ended or gap > 1.2 or too_long or i == n - 1:
            seg = words[start_idx:i + 1]
            sentences.append({
                "i": len(sentences),
                "s": seg[0]["s"],
                "e": seg[-1]["e"],
                "text": " ".join(x["w"] for x in seg),
                "w0": start_idx,
                "w1": i,
            })
            start_idx = i + 1
    return sentences
