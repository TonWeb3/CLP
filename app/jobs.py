"""Background work: one worker thread runs analysis and render jobs one at a time (GPU friendly)."""
from __future__ import annotations

import json
import logging
import queue
import shutil
import threading
import time
import traceback
from pathlib import Path

from . import config, db, models_mgr, youtube
from .pipeline import analyze, download, media, render, transcribe

log = logging.getLogger("clipforge.jobs")

Q: "queue.Queue[tuple[str, int]]" = queue.Queue()
CANCELLED: set[int] = set()
_started = False

# stage -> (low, high) share of the overall progress bar
RANGES = {"downloading": (0.0, 0.15), "audio": (0.15, 0.18), "transcribing": (0.18, 0.68), "analyzing": (0.68, 1.0)}
LABELS = {"downloading": "Downloading video", "audio": "Extracting audio",
          "transcribing": "Transcribing speech", "analyzing": "Finding moments"}


class Cancelled(Exception):
    pass


def video_dir(video_id: int) -> Path:
    d = config.DATA_DIR / "videos" / str(video_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def enqueue(kind: str, row_id: int) -> None:
    Q.put((kind, row_id))


def start() -> None:
    global _started
    if _started:
        return
    _started = True
    _recover()
    threading.Thread(target=_loop, daemon=True, name="clipforge-worker").start()


def _recover() -> None:
    """After a restart, anything that was running is marked failed so the user can retry."""
    for st in ("queued", "downloading", "audio", "transcribing", "analyzing"):
        db.ex("UPDATE videos SET status='failed', error=? WHERE status=?",
              ("Interrupted when the server restarted. Press Retry.", st))
    db.ex("UPDATE clips SET status='failed', error=? WHERE status IN ('queued','rendering')",
          ("Interrupted when the server restarted. Render it again.",))
    db.ex("UPDATE clips SET yt_status='failed', yt_error=? WHERE yt_status='uploading'",
          ("Interrupted when the server restarted. Upload again.",))


def _loop() -> None:
    while True:
        kind, row_id = Q.get()
        try:
            if kind == "analyze":
                _run_analyze(row_id)
            elif kind == "render":
                _run_render(row_id)
        except Exception:  # noqa: BLE001
            log.error("job crashed:\n%s", traceback.format_exc())


# --------------------------------------------------------------------------
# Analysis
# --------------------------------------------------------------------------
def _run_analyze(vid: int) -> None:
    v = db.one("SELECT * FROM videos WHERE id=?", (vid,))
    if not v or v["status"] == "cancelled":
        return
    cfg = config.get()

    def cancel_check():
        if vid in CANCELLED:
            raise Cancelled()

    def prog(stage: str, frac: float, msg: str | None = None):
        lo, hi = RANGES[stage]
        db.update("videos", vid, status=stage, stage_label=msg or LABELS[stage],
                  progress=round(lo + (hi - lo) * max(0.0, min(frac, 1.0)), 4))

    try:
        wd = video_dir(vid)
        cancel_check()

        # 1. download (or reuse)
        if not v["path"] or not Path(v["path"]).exists():
            if v["source"] != "youtube":
                raise RuntimeError("The uploaded file is missing. Upload it again.")
            prog("downloading", 0.0)
            res = download.download(v["url"], wd, cfg, lambda f, m: prog("downloading", f, m), cancel_check)
            db.update("videos", vid, path=res["path"], title=res["title"], duration=res["duration"])
            v = db.one("SELECT * FROM videos WHERE id=?", (vid,))
        info = media.probe(v["path"])
        if not info["has_audio"]:
            raise RuntimeError("This video has no audio track, so there is nothing to transcribe.")
        db.update("videos", vid, duration=info["duration"])
        if not v.get("thumb_path") or not Path(v["thumb_path"]).exists():
            tp = wd / "thumb.jpg"
            if media.make_thumb(v["path"], tp, at=min(5.0, info["duration"] / 3)):
                db.update("videos", vid, thumb_path=str(tp))

        # 2. audio
        cancel_check()
        audio = wd / "audio.wav"
        if not audio.exists():
            prog("audio", 0.0)
            media.extract_audio(v["path"], audio)
        db.update("videos", vid, audio_path=str(audio))

        # 3. transcript (cached)
        tpath = wd / "transcript.json"
        if tpath.exists():
            tr = json.loads(tpath.read_text("utf-8"))
        else:
            prog("transcribing", 0.0)
            words, lang = transcribe.transcribe(str(audio), cfg, info["duration"],
                                                lambda f, m: (cancel_check(), prog("transcribing", f, m)))
            if len(words) < 20:
                raise RuntimeError("Almost no speech was detected in this video.")
            tr = {"language": lang, "words": words}
            tr["sentences"] = transcribe.build_sentences(words)
            tpath.write_text(json.dumps(tr), "utf-8")
        db.update("videos", vid, transcript_path=str(tpath))

        # 4. analyse
        cancel_check()
        prog("analyzing", 0.0)
        curve = media.energy_curve(audio)
        cands = analyze.find_clips(cfg, tr["sentences"], info["duration"], curve,
                                   lambda f, m: prog("analyzing", f, m), cancel_check)
        if cfg.get("unload_models"):
            for m in {cfg.get("filter_model"), cfg.get("ranker_model")} - {""}:
                if cfg.get("llm_provider") == "ollama":
                    models_mgr.ollama_unload(m)

        # replace old un-rendered candidates, keep rendered clips
        db.ex("DELETE FROM clips WHERE video_id=? AND status IN ('candidate','failed','queued')", (vid,))
        title = v.get("title") or f"Video {vid}"
        for rank, c in enumerate(cands, 1):
            db.insert("clips", video_id=vid, video_title=title, rank=rank, score=c["score"], title=c["title"],
                      hook=c["hook"], reason=c["reason"], start=round(c["start"], 2), end=round(c["end"], 2),
                      status="candidate")
        db.update("videos", vid, status="ready", stage_label=f"{len(cands)} clips found", progress=1.0, error=None)
    except Cancelled:
        db.update("videos", vid, status="cancelled", stage_label="Cancelled", error=None)
    except Exception as e:  # noqa: BLE001
        log.error("analysis failed for %s:\n%s", vid, traceback.format_exc())
        db.update("videos", vid, status="failed", error=str(e)[:600])
    finally:
        CANCELLED.discard(vid)


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------
def _run_render(clip_id: int) -> None:
    c = db.one("SELECT * FROM clips WHERE id=?", (clip_id,))
    if not c:
        return
    v = db.one("SELECT * FROM videos WHERE id=?", (c["video_id"],))
    try:
        if not v or not v["path"] or not Path(v["path"]).exists():
            raise RuntimeError("The source video is no longer on disk.")
        cfg = config.get()
        opts = json.loads(c["render_opts"] or "{}")
        words = json.loads(Path(v["transcript_path"]).read_text("utf-8"))["words"] if v["transcript_path"] else []
        db.update("clips", clip_id, status="rendering", progress=0.0, error=None)

        def prog(f, _m=None):
            db.update("clips", clip_id, progress=round(f, 3))

        res = render.render(c, v, words, opts, cfg, prog)
        old = c.get("path")
        if old and old != res["path"]:
            Path(old).unlink(missing_ok=True)
        db.update("clips", clip_id, status="ready", progress=1.0, path=res["path"], thumb_path=res["thumb"],
                  width=res["width"], height=res["height"], size=res["size"], error=None)
    except Exception as e:  # noqa: BLE001
        log.error("render failed for clip %s:\n%s", clip_id, traceback.format_exc())
        db.update("clips", clip_id, status="failed", error=str(e)[:600])


# --------------------------------------------------------------------------
# YouTube upload (own thread per upload, independent of the GPU worker)
# --------------------------------------------------------------------------
def start_upload(clip_id: int, title: str, description: str, tags: list[str], privacy: str) -> None:
    db.update("clips", clip_id, yt_status="uploading", yt_progress=0.0, yt_error=None)

    def run():
        c = db.one("SELECT * FROM clips WHERE id=?", (clip_id,))
        try:
            vid_id = youtube.upload(config.get(), c["path"], title, description, tags, privacy,
                                    lambda f: db.update("clips", clip_id, yt_progress=round(f, 3)))
            db.update("clips", clip_id, yt_status="done", yt_progress=1.0, yt_id=vid_id, yt_error=None)
        except Exception as e:  # noqa: BLE001
            log.error("upload failed for clip %s: %s", clip_id, e)
            db.update("clips", clip_id, yt_status="failed", yt_error=str(e)[:500])

    threading.Thread(target=run, daemon=True).start()


def delete_video_files(video_id: int) -> None:
    shutil.rmtree(config.DATA_DIR / "videos" / str(video_id), ignore_errors=True)
