"""ClipForge API server."""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import secrets
import shutil
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote, urlparse

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, db, jobs, models_mgr, tiktok, youtube
from .pipeline import llm, media

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("clipforge")

STATIC = config.BASE_DIR / "static"
PASSWORD = os.environ.get("CLIPFORGE_PASSWORD", "")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    config.ensure_dirs()
    db.init()
    jobs.start()
    log.info("Data folder: %s", config.DATA_DIR)
    yield


app = FastAPI(title="ClipForge", lifespan=lifespan)


@app.middleware("http")
async def guard(request: Request, call_next):
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        if origin and urlparse(origin).netloc != request.headers.get("host"):
            return JSONResponse({"detail": "Cross-origin request blocked."}, status_code=403)
    if PASSWORD:
        auth = request.headers.get("authorization", "")
        ok = False
        if auth.lower().startswith("basic "):
            try:
                _user, _, pw = base64.b64decode(auth[6:]).decode("utf-8").partition(":")
                ok = secrets.compare_digest(pw, PASSWORD)
            except Exception:  # noqa: BLE001
                ok = False
        if not ok:
            return Response("Password required", status_code=401, headers={"WWW-Authenticate": 'Basic realm="ClipForge"'})
    resp = await call_next(request)
    if request.url.path.startswith("/static/") or request.url.path == "/":
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
    return resp


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def ranged_file(request: Request, path: str, media_type: str, download_name: str | None = None):
    p = Path(path)
    if not p.exists():
        raise HTTPException(404, "File not found")
    size = p.stat().st_size
    start, end, status = 0, size - 1, 200
    rng = request.headers.get("range")
    if rng and rng.startswith("bytes="):
        s, _, e = rng[6:].split(",")[0].partition("-")
        try:
            if s == "":
                start = max(0, size - int(e))
            else:
                start = int(s)
                if e:
                    end = min(int(e), size - 1)
        except ValueError:
            raise HTTPException(416, "Bad range")
        if start > end or start >= size:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
        status = 206
    length = end - start + 1

    def it():
        with open(p, "rb") as f:
            f.seek(start)
            left = length
            while left > 0:
                chunk = f.read(min(1 << 20, left))
                if not chunk:
                    break
                left -= len(chunk)
                yield chunk

    headers = {"Accept-Ranges": "bytes", "Content-Length": str(length)}
    if status == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    if download_name:
        headers["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(download_name)}"
    return StreamingResponse(it(), status_code=status, media_type=media_type, headers=headers)


def _video_row(v: dict) -> dict:
    out = {k: v[k] for k in ("id", "url", "source", "title", "duration", "status", "stage_label", "progress",
                             "error", "created", "updated")}
    out["has_thumb"] = bool(v.get("thumb_path") and Path(v["thumb_path"]).exists())
    out["has_source"] = bool(v.get("path") and Path(v["path"]).exists())
    return out


def _clip_row(c: dict) -> dict:
    keys = ("id", "video_id", "video_title", "rank", "score", "title", "hook", "reason", "start", "end", "selected",
            "status", "progress", "error", "width", "height", "size", "yt_status", "yt_progress", "yt_id",
            "yt_error", "tiktok_status", "tiktok_progress", "tiktok_id", "tiktok_error", "created", "updated")
    out = {k: c.get(k) for k in keys}
    out["selected"] = bool(c.get("selected"))
    out["has_thumb"] = bool(c.get("thumb_path") and Path(c["thumb_path"]).exists())
    out["path"] = c.get("path")
    out["social_copy"] = json.loads(c["social_copy"]) if c.get("social_copy") else None
    try:
        out["render_opts"] = json.loads(c.get("render_opts") or "{}")
    except Exception:
        out["render_opts"] = {}
    try:
        out["uploads"] = json.loads(c.get("uploads_json") or "{}")
    except Exception:
        out["uploads"] = {}
    return out


def _account_row(a: dict, base_url: str) -> dict:
    plat = a["platform"]
    acc_id = a["id"]
    is_conn = youtube.connected(acc_id) if plat == "youtube" else tiktok.connected(acc_id)
    red_uri = youtube.redirect_uri_for(base_url) if plat == "youtube" else tiktok.redirect_uri_for(base_url)
    default_priv = "private" if plat == "youtube" else "INBOX"
    return {
        "id": acc_id,
        "platform": plat,
        "name": a["name"],
        "username": a.get("username") or "",
        "scope": a.get("scope") or "",
        "client_id": a.get("client_id") or "",
        "client_secret_set": bool(a.get("client_secret")),
        "has_keys": bool(a.get("client_id") and a.get("client_secret")),
        "privacy": a.get("privacy") or default_priv,
        "add_shorts_tag": bool(a.get("add_shorts_tag", 1)),
        "connected": is_conn,
        "redirect_uri": red_uri,
        "created": a.get("created"),
        "updated": a.get("updated"),
    }


def _get_video(vid: int) -> dict:
    v = db.one("SELECT * FROM videos WHERE id=?", (vid,))
    if not v:
        raise HTTPException(404, "Video not found")
    return v


def _get_clip(cid: int) -> dict:
    c = db.one("SELECT * FROM clips WHERE id=?", (cid,))
    if not c:
        raise HTTPException(404, "Clip not found")
    return c


# --------------------------------------------------------------------------
# health + settings
# --------------------------------------------------------------------------
@app.get("/api/health")
def health():
    cfg = config.get()
    checks = {
        "ffmpeg": media.have_ffmpeg(),
        "yt_dlp": models_mgr.ytdlp_version(),
        "faster_whisper": models_mgr.faster_whisper_version(),
        "gpus": models_mgr.gpu_info(),
        "encoder": media.pick_encoder(cfg["encoder"]) if media.have_ffmpeg() else None,
        "whisper_model": cfg["whisper_model"],
        "whisper_ready": models_mgr.whisper_downloaded(cfg["whisper_model"]),
        "llm_provider": cfg["llm_provider"],
        "filter_model": cfg["filter_model"],
        "ranker_model": cfg["ranker_model"],
        "vision_model": cfg.get("vision_model", ""),
        "face_tracker": models_mgr.face_tracker_info(),
        "llm": None,
        "llm_models": [],
        "youtube_connected": youtube.connected(),
        "tiktok_connected": tiktok.connected(),
        "clips_dir": str(config.clips_dir()),
        "data_dir": str(config.DATA_DIR),
    }
    t = llm.test_connection(cfg)
    checks["llm"] = t["ok"]
    checks["llm_message"] = t["message"]
    checks["llm_models"] = t.get("models", [])
    if cfg["llm_provider"] == "ollama":
        checks["ollama_version"] = models_mgr.ollama_version()
    return checks


@app.get("/api/settings")
def get_settings():
    return {"settings": config.public(), "defaults": {k: v for k, v in config.DEFAULTS.items()
                                                     if k not in config.SECRET_KEYS}}


@app.post("/api/settings")
async def save_settings(request: Request):
    patch = await request.json()
    if not isinstance(patch, dict):
        raise HTTPException(400, "Expected an object")
    if patch.get("clips_dir"):
        try:
            Path(patch["clips_dir"]).expanduser().mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise HTTPException(400, f"Cannot use that folder: {e}")
    config.update(patch)
    config.ensure_dirs()
    return {"settings": config.public()}


@app.post("/api/llm/test")
def llm_test():
    return llm.test_connection(config.get())


# --------------------------------------------------------------------------
# models
# --------------------------------------------------------------------------
class NameBody(BaseModel):
    name: str


@app.get("/api/models/ollama")
def ollama_models():
    try:
        return {"ok": True, "models": models_mgr.ollama_list(), "version": models_mgr.ollama_version()}
    except Exception:  # noqa: BLE001
        return {"ok": False, "models": [], "version": None,
                "message": f"Cannot reach Ollama at {config.get()['ollama_host']}. Install and start it, "
                           "or fix the address in Settings."}


@app.post("/api/models/ollama/pull")
def ollama_pull(body: NameBody):
    try:
        models_mgr.ollama_pull(body.name)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.delete("/api/models/ollama")
def ollama_delete(name: str):
    try:
        models_mgr.ollama_delete(name)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.get("/api/models/status")
def models_status():
    return models_mgr.pulls_status()


@app.get("/api/models/whisper")
def whisper_models():
    return {"models": models_mgr.whisper_list(), "installed": models_mgr.faster_whisper_version() is not None}


@app.post("/api/models/whisper/download")
def whisper_download(body: NameBody):
    try:
        models_mgr.whisper_download(body.name)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.delete("/api/models/whisper/{name}")
def whisper_delete(name: str):
    if name not in models_mgr.WHISPER_MODELS:
        raise HTTPException(404, "Unknown model")
    models_mgr.whisper_delete(name)
    return {"ok": True}


# --------------------------------------------------------------------------
# videos
# --------------------------------------------------------------------------
class UrlsBody(BaseModel):
    urls: list[str]


@app.post("/api/videos")
def add_videos(body: UrlsBody):
    ids = []
    seen = set()
    for u in body.urls:
        u = u.strip()
        if not re.match(r"^https?://", u, re.I) or u in seen:
            continue
        seen.add(u)
        vid = db.insert("videos", url=u, source="youtube", title=u, status="queued", stage_label="Waiting in queue",
                        progress=0.0)
        jobs.enqueue("analyze", vid)
        ids.append(vid)
    if not ids:
        raise HTTPException(400, "No valid links found. Links must start with http:// or https://")
    return {"ids": ids}


@app.post("/api/videos/upload")
async def upload_video(file: UploadFile = File(...)):
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", file.filename or "video.mp4")
    ext = Path(name).suffix.lower() or ".mp4"
    vid = db.insert("videos", url="", source="upload", title=Path(file.filename or "Upload").stem, status="queued",
                    stage_label="Waiting in queue", progress=0.0)
    dest = jobs.video_dir(vid) / f"source{ext}"
    with open(dest, "wb") as f:
        while chunk := await file.read(1 << 20):
            f.write(chunk)
    db.update("videos", vid, path=str(dest))
    jobs.enqueue("analyze", vid)
    return {"ids": [vid]}


@app.get("/api/videos")
def list_videos():
    return {"videos": [_video_row(v) for v in db.q("SELECT * FROM videos ORDER BY id DESC")]}


@app.get("/api/videos/{vid}")
def get_video(vid: int):
    v = _get_video(vid)
    clips = db.q("SELECT * FROM clips WHERE video_id=? ORDER BY rank IS NULL, rank, id", (vid,))
    return {"video": _video_row(v), "clips": [_clip_row(c) for c in clips]}


@app.post("/api/videos/{vid}/retry")
def retry_video(vid: int):
    v = _get_video(vid)
    if v["status"] not in ("failed", "cancelled", "ready"):
        raise HTTPException(400, "This video is still being processed.")
    jobs.CANCELLED.discard(vid)
    db.update("videos", vid, status="queued", stage_label="Waiting in queue", progress=0.0, error=None)
    jobs.enqueue("analyze", vid)
    return {"ok": True}


@app.post("/api/videos/{vid}/cancel")
def cancel_video(vid: int):
    v = _get_video(vid)
    if v["status"] in ("queued", "downloading", "audio", "transcribing", "analyzing"):
        jobs.CANCELLED.add(vid)
        if v["status"] == "queued":
            db.update("videos", vid, status="cancelled", stage_label="Cancelled")
    return {"ok": True}


@app.delete("/api/videos/{vid}")
def delete_video(vid: int):
    v = _get_video(vid)
    if v["status"] in ("downloading", "audio", "transcribing", "analyzing"):
        raise HTTPException(400, "Cancel the job before deleting it.")
    db.ex("DELETE FROM clips WHERE video_id=? AND status!='ready'", (vid,))
    jobs.delete_video_files(vid)
    db.ex("DELETE FROM videos WHERE id=?", (vid,))
    return {"ok": True}


@app.get("/api/videos/{vid}/source")
def video_source(vid: int, request: Request):
    v = _get_video(vid)
    if not v["path"]:
        raise HTTPException(404, "No source yet")
    ext = Path(v["path"]).suffix.lower()
    mt = {".mp4": "video/mp4", ".m4v": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime",
          ".mkv": "video/x-matroska"}.get(ext, "application/octet-stream")
    return ranged_file(request, v["path"], mt)


@app.get("/api/videos/{vid}/thumb")
def video_thumb(vid: int):
    v = _get_video(vid)
    if not v.get("thumb_path") or not Path(v["thumb_path"]).exists():
        raise HTTPException(404, "No thumbnail")
    return FileResponse(v["thumb_path"], media_type="image/jpeg")


class RenderBody(BaseModel):
    clip_ids: list[int]
    aspect: str | None = None
    reframe: str | None = None
    captions: bool | None = None
    caption_preset: str | None = None
    caption_emojis: bool | None = None
    headline: bool | None = None
    normalize_audio: bool | None = None
    clean_audio: bool | None = None
    dynamic_zoom: bool | None = None
    remove_silence: bool | None = None


@app.post("/api/videos/{vid}/render")
def render_clips(vid: int, body: RenderBody):
    v = _get_video(vid)
    if not v["path"] or not Path(v["path"]).exists():
        raise HTTPException(400, "The source video is no longer on disk. Retry the video to download it again.")
    opts = {k: val for k, val in body.model_dump().items() if k != "clip_ids" and val is not None}
    if opts.get("aspect") not in (None, "9:16", "1:1", "4:5", "16:9"):
        raise HTTPException(400, "Unknown aspect ratio")
    if opts.get("reframe") not in (None, "face", "split", "center", "blur"):
        raise HTTPException(400, "Unknown reframe mode")
    if opts.get("caption_preset") not in (None, "classic", "hormozi", "mrbeast", "minimalist"):
        raise HTTPException(400, "Unknown caption preset")
    n = 0
    for cid in body.clip_ids:
        c = db.one("SELECT * FROM clips WHERE id=? AND video_id=?", (cid, vid))
        if not c or c["status"] in ("queued", "rendering"):
            continue
        db.update("clips", cid, status="queued", progress=0.0, error=None, render_opts=json.dumps(opts))
        jobs.enqueue("render", cid)
        n += 1
    return {"queued": n}


# --------------------------------------------------------------------------
# clips
# --------------------------------------------------------------------------
class ClipPatch(BaseModel):
    title: str | None = None
    hook: str | None = None
    start: float | None = None
    end: float | None = None
    selected: bool | None = None


@app.patch("/api/clips/{cid}")
def patch_clip(cid: int, body: ClipPatch):
    c = _get_clip(cid)
    fields = {}
    if body.title is not None:
        fields["title"] = body.title.strip()[:150]
    if body.hook is not None:
        fields["hook"] = body.hook.strip()[:400]
    if body.selected is not None:
        fields["selected"] = 1 if body.selected else 0
    s = body.start if body.start is not None else c["start"]
    e = body.end if body.end is not None else c["end"]
    if body.start is not None or body.end is not None:
        if s < 0 or e - s < 1:
            raise HTTPException(400, "The end must be at least one second after the start.")
        fields["start"], fields["end"] = round(s, 2), round(e, 2)
    db.update("clips", cid, **fields)
    return _clip_row(_get_clip(cid))


class NewClip(BaseModel):
    video_id: int
    start: float
    end: float
    title: str = "Custom clip"


@app.post("/api/clips")
def create_clip(body: NewClip):
    v = _get_video(body.video_id)
    if body.end - body.start < 1 or body.start < 0:
        raise HTTPException(400, "The end must be at least one second after the start.")
    cid = db.insert("clips", video_id=v["id"], video_title=v["title"], rank=None, score=None,
                    title=body.title.strip()[:150] or "Custom clip", hook="", reason="Added by you",
                    start=round(body.start, 2), end=round(body.end, 2), selected=1, status="candidate")
    return _clip_row(_get_clip(cid))


@app.get("/api/clips")
def list_clips(status: str = "ready"):
    rows = db.q("SELECT * FROM clips WHERE status=? ORDER BY updated DESC", (status,))
    return {"clips": [_clip_row(c) for c in rows], "folder": str(config.clips_dir())}


@app.get("/api/clips/{cid}/file")
def clip_file(cid: int, request: Request, download: int = 0):
    c = _get_clip(cid)
    if c["status"] != "ready" or not c["path"]:
        raise HTTPException(404, "Clip is not rendered yet")
    name = re.sub(r"[^A-Za-z0-9._ -]+", "", c["title"] or "clip").strip()[:80] or "clip"
    return ranged_file(request, c["path"], "video/mp4", f"{name}.mp4" if download else None)


@app.get("/api/clips/{cid}/thumb")
def clip_thumb(cid: int):
    c = _get_clip(cid)
    if not c.get("thumb_path") or not Path(c["thumb_path"]).exists():
        raise HTTPException(404, "No thumbnail")
    return FileResponse(c["thumb_path"], media_type="image/jpeg")


@app.delete("/api/clips/{cid}")
def delete_clip(cid: int):
    c = _get_clip(cid)
    if c["status"] in ("queued", "rendering") or c["yt_status"] == "uploading":
        raise HTTPException(400, "This clip is busy. Wait for it to finish.")
    for p in (c.get("path"), c.get("thumb_path")):
        if p:
            Path(p).unlink(missing_ok=True)
    if c["status"] == "ready" and db.one("SELECT 1 AS x FROM videos WHERE id=?", (c["video_id"],)):
        # keep the suggestion so it can be rendered again
        db.update("clips", cid, status="candidate", path=None, thumb_path=None, progress=0.0, size=None,
                  yt_status=None, yt_id=None, yt_error=None)
    else:
        db.ex("DELETE FROM clips WHERE id=?", (cid,))
    return {"ok": True}


class UploadBody(BaseModel):
    title: str
    description: str = ""
    tags: list[str] = []
    privacy: str = "private"
    add_shorts_tag: bool = True
    account_id: int | None = None
    account_ids: list[int] = []


@app.post("/api/clips/{cid}/youtube")
def upload_clip(cid: int, body: UploadBody):
    c = _get_clip(cid)
    if c["status"] != "ready" or not c["path"]:
        raise HTTPException(400, "Render the clip first.")
    targets = list(body.account_ids) if body.account_ids else ([body.account_id] if body.account_id else [])
    if targets:
        for aid in targets:
            if not youtube.connected(aid):
                acc = db.one("SELECT name FROM social_accounts WHERE id=?", (aid,))
                label = acc["name"] if acc else f"#{aid}"
                raise HTTPException(400, f"YouTube account '{label}' is not connected. Connect it in Settings.")
    else:
        if not youtube.connected():
            raise HTTPException(400, "YouTube is not connected. Connect an account in Settings.")
        targets = [None]

    title = body.title.strip()
    desc = body.description.strip()
    vertical = (c["height"] or 0) > (c["width"] or 0)
    if body.add_shorts_tag and vertical and "#shorts" not in (title + desc).lower():
        if len(title) + 8 <= 100:
            title += " #Shorts"
        else:
            desc = (desc + "\n\n#Shorts").strip()
    for aid in targets:
        jobs.start_upload(cid, title, desc, body.tags, body.privacy, account_id=aid)
    return {"ok": True}


# --------------------------------------------------------------------------
# TikTok upload & connection
# --------------------------------------------------------------------------
class TikTokUploadBody(BaseModel):
    title: str = ""
    privacy: str = "INBOX"
    account_id: int | None = None
    account_ids: list[int] = []


@app.post("/api/clips/{cid}/tiktok")
def upload_clip_tiktok(cid: int, body: TikTokUploadBody):
    c = _get_clip(cid)
    if c["status"] != "ready" or not c["path"]:
        raise HTTPException(400, "Render the clip first.")
    targets = list(body.account_ids) if body.account_ids else ([body.account_id] if body.account_id else [])
    if targets:
        for aid in targets:
            if not tiktok.connected(aid):
                acc = db.one("SELECT name FROM social_accounts WHERE id=?", (aid,))
                label = acc["name"] if acc else f"#{aid}"
                raise HTTPException(400, f"TikTok account '{label}' is not connected. Connect it in Settings.")
    else:
        if not tiktok.connected():
            raise HTTPException(400, "TikTok is not connected. Connect an account in Settings.")
        targets = [None]

    for aid in targets:
        jobs.start_tiktok_upload(cid, body.title.strip(), body.privacy, account_id=aid)
    return {"ok": True}


# --------------------------------------------------------------------------
# Multi-Account Management (Named YouTube & TikTok APIs)
# --------------------------------------------------------------------------
class AccountCreate(BaseModel):
    platform: str  # "youtube" or "tiktok"
    name: str
    client_id: str = ""
    client_secret: str = ""
    privacy: str = ""
    scope: str = ""
    add_shorts_tag: bool = True


class AccountPatch(BaseModel):
    name: str | None = None
    client_id: str | None = None
    client_secret: str | None = None
    privacy: str | None = None
    scope: str | None = None
    add_shorts_tag: bool | None = None


@app.get("/api/accounts")
def list_accounts(request: Request):
    base = str(request.base_url)
    rows = db.q("SELECT * FROM social_accounts ORDER BY platform ASC, id ASC")
    return {
        "accounts": [_account_row(r, base) for r in rows],
        "redirect_uris": {
            "youtube": youtube.redirect_uri_for(base),
            "tiktok": tiktok.redirect_uri_for(base),
        },
    }


@app.post("/api/accounts")
def create_account(body: AccountCreate, request: Request):
    plat = body.platform.strip().lower()
    if plat not in ("youtube", "tiktok"):
        raise HTTPException(400, "Platform must be 'youtube' or 'tiktok'.")
    name = body.name.strip()[:80]
    if not name:
        raise HTTPException(400, "Give this API account a name so you can identify it.")
    default_priv = "private" if plat == "youtube" else "INBOX"
    priv = body.privacy.strip() or default_priv
    aid = db.insert(
        "social_accounts",
        platform=plat,
        name=name,
        client_id=body.client_id.strip(),
        client_secret=body.client_secret.strip(),
        privacy=priv,
        scope=body.scope.strip(),
        add_shorts_tag=1 if body.add_shorts_tag else 0,
    )
    row = db.one("SELECT * FROM social_accounts WHERE id=?", (aid,))
    return {"account": _account_row(row, str(request.base_url))}


@app.patch("/api/accounts/{acc_id}")
def update_account(acc_id: int, body: AccountPatch, request: Request):
    row = db.one("SELECT * FROM social_accounts WHERE id=?", (acc_id,))
    if not row:
        raise HTTPException(404, "Account not found")
    fields = {}
    if body.name is not None:
        n = body.name.strip()[:80]
        if not n:
            raise HTTPException(400, "Account name cannot be empty.")
        fields["name"] = n
    if body.client_id is not None:
        fields["client_id"] = body.client_id.strip()
    if body.client_secret is not None and body.client_secret != "" and body.client_secret != config.MASK:
        fields["client_secret"] = body.client_secret.strip()
    if body.privacy is not None:
        fields["privacy"] = body.privacy.strip()
    if body.scope is not None:
        fields["scope"] = body.scope.strip()
    if body.add_shorts_tag is not None:
        fields["add_shorts_tag"] = 1 if body.add_shorts_tag else 0
    if fields:
        db.update("social_accounts", acc_id, **fields)
    updated = db.one("SELECT * FROM social_accounts WHERE id=?", (acc_id,))
    return {"account": _account_row(updated, str(request.base_url))}


@app.delete("/api/accounts/{acc_id}")
def delete_account(acc_id: int):
    row = db.one("SELECT * FROM social_accounts WHERE id=?", (acc_id,))
    if not row:
        raise HTTPException(404, "Account not found")
    if row["platform"] == "youtube":
        youtube.disconnect(acc_id)
    else:
        tiktok.disconnect(acc_id)
    db.ex("DELETE FROM social_accounts WHERE id=?", (acc_id,))
    return {"ok": True}


@app.get("/api/accounts/{acc_id}/connect")
def connect_account(acc_id: int, request: Request):
    row = db.one("SELECT * FROM social_accounts WHERE id=?", (acc_id,))
    if not row:
        raise HTTPException(404, "Account not found")
    base = str(request.base_url)
    try:
        if row["platform"] == "youtube":
            url = youtube.auth_url(row, youtube.redirect_uri_for(base))
        else:
            url = tiktok.auth_url(row, tiktok.redirect_uri_for(base), scope=row.get("scope"))
    except (youtube.YouTubeError, tiktok.TikTokError) as e:
        raise HTTPException(400, str(e))
    return {"url": url}


@app.get("/api/accounts/{acc_id}/creator-info")
def account_creator_info(acc_id: int):
    row = db.one("SELECT * FROM social_accounts WHERE id=?", (acc_id,))
    if not row or row["platform"] != "tiktok":
        raise HTTPException(404, "TikTok account not found")
    return tiktok.get_creator_info(row)


@app.post("/api/accounts/{acc_id}/disconnect")
def disconnect_account(acc_id: int):
    row = db.one("SELECT * FROM social_accounts WHERE id=?", (acc_id,))
    if not row:
        raise HTTPException(404, "Account not found")
    if row["platform"] == "youtube":
        youtube.disconnect(acc_id)
    else:
        tiktok.disconnect(acc_id)
    return {"ok": True}


@app.get("/api/tiktok/status")
def tiktok_status_api(request: Request):
    cfg = config.get()
    rows = db.q("SELECT * FROM social_accounts WHERE platform='tiktok' ORDER BY id ASC")
    has_keys = any(r.get("client_id") and r.get("client_secret") for r in rows) or bool(
        cfg.get("tiktok_client_key") and cfg.get("tiktok_client_secret")
    )
    return {
        "connected": tiktok.connected(),
        "has_keys": has_keys,
        "redirect_uri": tiktok.redirect_uri_for(str(request.base_url)),
    }


@app.get("/api/tiktok/connect")
def tiktok_connect_api(request: Request, account_id: int | None = None):
    try:
        target = account_id if account_id is not None else config.get()
        url = tiktok.auth_url(target, tiktok.redirect_uri_for(str(request.base_url)))
    except tiktok.TikTokError as e:
        raise HTTPException(400, str(e))
    return {"url": url}


@app.get("/api/tiktok/callback")
def tiktok_callback_api(request: Request, state: str = "", code: str = "", error: str = "", error_description: str = ""):
    if error or not code:
        err_msg = error_description or error or "Sign-in was cancelled"
        return RedirectResponse(f"/#/settings?tiktok=error&msg={quote(err_msg)}")
    try:
        tiktok.finish(state, code, tiktok.redirect_uri_for(str(request.base_url)))
    except Exception as e:  # noqa: BLE001
        return RedirectResponse(f"/#/settings?tiktok=error&msg={quote(str(e)[:200])}")
    return RedirectResponse("/#/settings?tiktok=ok")


@app.post("/api/tiktok/disconnect")
def tiktok_disconnect_api(account_id: int | None = None):
    tiktok.disconnect(account_id)
    return {"ok": True}


# --------------------------------------------------------------------------
# AI Social Post Copy Generator
# --------------------------------------------------------------------------
@app.post("/api/clips/{cid}/social-copy")
def generate_social_copy_api(cid: int):
    c = _get_clip(cid)
    v = _get_video(c["video_id"])
    words = []
    if v.get("transcript_path") and Path(v["transcript_path"]).exists():
        try:
            words = json.loads(Path(v["transcript_path"]).read_text("utf-8")).get("words", [])
        except Exception:
            pass
    clip_words = [w["w"] for w in words if w.get("e", 0) > c["start"] and w.get("s", 0) < c["end"]]
    snippet = " ".join(clip_words) if clip_words else (c.get("hook") or c.get("title") or "")
    try:
        copy_data = llm.generate_social_post(config.get(), c.get("title") or "Clip", c.get("hook") or "", snippet)
        db.update("clips", cid, social_copy=json.dumps(copy_data))
        return {"ok": True, "social_copy": copy_data}
    except Exception as e:
        raise HTTPException(400, str(e))


# --------------------------------------------------------------------------
# Scheduled Automated Publishing
# --------------------------------------------------------------------------
class ScheduleCreate(BaseModel):
    clip_ids: list[int]
    platforms: list[str] = []  # legacy support: ["youtube", "tiktok"]
    account_ids: list[int] = []  # default named account IDs for all selected clips
    clip_accounts: dict[str, list[int]] = {}  # per-clip override: {"<clip_id>": [acc_id1, acc_id2]}
    mode: str = "time"  # "time" or "interval"
    start_time: float  # unix timestamp
    interval_minutes: int = 60
    options: dict = {}


@app.get("/api/scheduled")
def list_scheduled():
    rows = db.q(
        "SELECT s.*, c.title AS clip_title, c.thumb_path, (c.end - c.start) AS clip_dur, c.width, c.height, "
        "c.video_title, c.path AS clip_path "
        "FROM scheduled_uploads s "
        "LEFT JOIN clips c ON s.clip_id = c.id "
        "ORDER BY s.scheduled_time ASC"
    )
    acc_map = {a["id"]: a for a in db.q("SELECT id, name, platform FROM social_accounts")}
    out = []
    for r in rows:
        d = dict(r)
        d["platforms"] = json.loads(d.get("platforms") or "[]")
        d["accounts"] = json.loads(d.get("accounts") or "[]")
        d["options"] = json.loads(d.get("options") or "{}")
        d["results"] = json.loads(d.get("results") or "{}")
        d["has_thumb"] = bool(d.get("thumb_path") and Path(d["thumb_path"]).exists())
        details = []
        for aid in d["accounts"]:
            if aid in acc_map:
                details.append(acc_map[aid])
            else:
                details.append({"id": aid, "name": f"Account #{aid}", "platform": "unknown"})
        d["account_details"] = details
        out.append(d)
    return {"scheduled": out}


@app.post("/api/scheduled")
def create_scheduled(body: ScheduleCreate):
    if not body.clip_ids:
        raise HTTPException(400, "No clips selected.")

    has_any_target = bool(body.account_ids or body.platforms or any(body.clip_accounts.values()))
    if not has_any_target:
        raise HTTPException(400, "Select at least one destination API account.")

    acc_map = {a["id"]: a for a in db.q("SELECT * FROM social_accounts")}
    start_t = max(time.time(), float(body.start_time))
    interval_sec = max(1, int(body.interval_minutes)) * 60
    created_ids = []

    for idx, cid in enumerate(body.clip_ids):
        c = db.one("SELECT * FROM clips WHERE id=?", (cid,))
        if not c or c["status"] != "ready":
            continue

        # Resolve target accounts for this specific clip
        clip_acc_ids = body.clip_accounts.get(str(cid))
        if clip_acc_ids is None:
            clip_acc_ids = list(body.account_ids)

        valid_acc_ids = [aid for aid in clip_acc_ids if aid in acc_map]
        plats = list({acc_map[aid]["platform"] for aid in valid_acc_ids}) if valid_acc_ids else list(body.platforms)
        if not valid_acc_ids and not plats:
            continue

        sch_time = start_t + (idx * interval_sec if body.mode == "interval" else 0)
        clip_opts = dict(body.options)
        for aid in valid_acc_ids:
            acc = acc_map[aid]
            akey = f"account_{aid}"
            if akey not in clip_opts:
                clip_opts[akey] = {
                    "title": c["title"],
                    "privacy": acc.get("privacy") or ("private" if acc["platform"] == "youtube" else "INBOX"),
                    "add_shorts_tag": bool(acc.get("add_shorts_tag", 1)),
                }

        sid = db.insert(
            "scheduled_uploads",
            clip_id=cid,
            platforms=json.dumps(plats),
            accounts=json.dumps(valid_acc_ids),
            scheduled_time=sch_time,
            status="pending",
            options=json.dumps(clip_opts),
            results="{}",
        )
        created_ids.append(sid)

    if not created_ids:
        raise HTTPException(400, "No valid ready clips or accounts selected to schedule.")
    return {"created": len(created_ids), "ids": created_ids}


@app.delete("/api/scheduled/{sched_id}")
def delete_scheduled(sched_id: int):
    row = db.one("SELECT * FROM scheduled_uploads WHERE id=?", (sched_id,))
    if not row:
        raise HTTPException(404, "Scheduled upload not found")
    if row["status"] == "uploading":
        raise HTTPException(400, "Cannot delete an upload currently in progress.")
    db.ex("DELETE FROM scheduled_uploads WHERE id=?", (sched_id,))
    return {"ok": True}


@app.post("/api/scheduled/{sched_id}/retry")
def retry_scheduled(sched_id: int):
    row = db.one("SELECT * FROM scheduled_uploads WHERE id=?", (sched_id,))
    if not row:
        raise HTTPException(404, "Scheduled upload not found")
    db.update("scheduled_uploads", sched_id, status="pending", scheduled_time=time.time(), error=None)
    return {"ok": True}


# --------------------------------------------------------------------------
# YouTube connection
# --------------------------------------------------------------------------
@app.get("/api/youtube/status")
def yt_status(request: Request):
    cfg = config.get()
    rows = db.q("SELECT * FROM social_accounts WHERE platform='youtube' ORDER BY id ASC")
    has_keys = any(r.get("client_id") and r.get("client_secret") for r in rows) or bool(
        cfg.get("yt_client_id") and cfg.get("yt_client_secret")
    )
    return {
        "connected": youtube.connected(),
        "has_keys": has_keys,
        "redirect_uri": youtube.redirect_uri_for(str(request.base_url)),
    }


@app.get("/api/youtube/connect")
def yt_connect(request: Request, account_id: int | None = None):
    try:
        target = account_id if account_id is not None else config.get()
        url = youtube.auth_url(target, youtube.redirect_uri_for(str(request.base_url)))
    except youtube.YouTubeError as e:
        raise HTTPException(400, str(e))
    return {"url": url}


@app.get("/api/youtube/callback")
def yt_callback(request: Request, state: str = "", code: str = "", error: str = ""):
    if error or not code:
        return RedirectResponse(f"/#/settings?yt=error&msg={quote(error or 'Sign-in was cancelled')}")
    try:
        youtube.finish(state, code)
    except Exception as e:  # noqa: BLE001
        return RedirectResponse(f"/#/settings?yt=error&msg={quote(str(e)[:200])}")
    return RedirectResponse("/#/settings?yt=ok")


@app.post("/api/youtube/disconnect")
def yt_disconnect(account_id: int | None = None):
    youtube.disconnect(account_id)
    return {"ok": True}


# --------------------------------------------------------------------------
# static UI
# --------------------------------------------------------------------------
@app.get("/")
def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")
