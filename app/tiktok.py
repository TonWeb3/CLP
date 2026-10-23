"""TikTok sign-in (OAuth 2.0) and direct video publishing via TikTok Content Posting API v2."""
from __future__ import annotations

import json
import logging
import os
import secrets
import threading
import time
from pathlib import Path
from urllib.parse import urlencode

import requests

from . import config

log = logging.getLogger("clipforge.tiktok")
TOKEN_FILE = config.DATA_DIR / "tiktok_token.json"
TIKTOK_AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TIKTOK_TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
TIKTOK_POST_INIT_URL = "https://open.tiktokapis.com/v2/post/publish/video/init/"
TIKTOK_STATUS_URL = "https://open.tiktokapis.com/v2/post/publish/status_fetch/"

_states: dict[str, float] = {}
_lock = threading.Lock()


class TikTokError(RuntimeError):
    pass


def redirect_uri_for(base_url: str) -> str:
    return base_url.rstrip("/") + "/api/tiktok/callback"


def auth_url(cfg: dict, redirect_uri: str) -> str:
    key = cfg.get("tiktok_client_key", "").strip()
    secret = cfg.get("tiktok_client_secret", "").strip()
    if not key or not secret:
        raise TikTokError("Add your TikTok Client Key and Client Secret in Settings first.")

    state = secrets.token_urlsafe(24)
    with _lock:
        now = time.time()
        # Clean up stale states (>1 hour)
        expired = [k for k, t in _states.items() if now - t > 3600]
        for k in expired:
            _states.pop(k, None)
        _states[state] = now

    params = {
        "client_key": key,
        "scope": "video.upload,video.publish",
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "state": state,
    }
    return f"{TIKTOK_AUTH_URL}?{urlencode(params)}"


def finish(state: str, code: str, redirect_uri: str) -> None:
    with _lock:
        saved_time = _states.pop(state, None)
    if not saved_time or (time.time() - saved_time > 3600):
        raise TikTokError("This sign-in link has expired. Start the connection again from Settings.")

    cfg = config.get()
    key = cfg.get("tiktok_client_key", "").strip()
    secret = cfg.get("tiktok_client_secret", "").strip()
    if not key or not secret:
        raise TikTokError("TikTok Client Key or Secret missing from Settings.")

    resp = requests.post(
        TIKTOK_TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "client_key": key,
            "client_secret": secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        },
        timeout=30,
    )
    data = resp.json()
    if resp.status_code != 200 or data.get("error") or not data.get("data", {}).get("access_token"):
        err = data.get("error_description") or data.get("message") or resp.text[:300]
        raise TikTokError(f"TikTok authentication failed: {err}")

    token_data = data["data"]
    token_data["created_at"] = time.time()
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(json.dumps(token_data, indent=2), "utf-8")
    try:
        os.chmod(TOKEN_FILE, 0o600)
    except OSError:
        pass


def connected() -> bool:
    return TOKEN_FILE.exists()


def disconnect() -> None:
    TOKEN_FILE.unlink(missing_ok=True)


def _get_access_token(cfg: dict) -> str:
    if not TOKEN_FILE.exists():
        raise TikTokError("TikTok is not connected. Connect it in Settings.")
    try:
        data = json.loads(TOKEN_FILE.read_text("utf-8"))
    except Exception as e:
        raise TikTokError("Failed to read TikTok token. Please reconnect in Settings.") from e

    token = data.get("access_token")
    expires_in = data.get("expires_in", 86400)
    created_at = data.get("created_at", 0)

    # If within 5 minutes of expiring, refresh
    if time.time() - created_at > (expires_in - 300) and data.get("refresh_token"):
        key = cfg.get("tiktok_client_key", "").strip()
        secret = cfg.get("tiktok_client_secret", "").strip()
        resp = requests.post(
            TIKTOK_TOKEN_URL,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "client_key": key,
                "client_secret": secret,
                "grant_type": "refresh_token",
                "refresh_token": data["refresh_token"],
            },
            timeout=30,
        )
        refresh_data = resp.json()
        if resp.status_code == 200 and refresh_data.get("data", {}).get("access_token"):
            data.update(refresh_data["data"])
            data["created_at"] = time.time()
            TOKEN_FILE.write_text(json.dumps(data, indent=2), "utf-8")
            token = data["access_token"]
        else:
            log.warning("TikTok token refresh failed; trying existing access token")

    if not token:
        raise TikTokError("TikTok token expired or missing. Reconnect in Settings.")
    return token


def upload(cfg: dict, video_path: str | Path, title: str, privacy: str, progress_cb) -> str:
    """Uploads a video to TikTok using the Content Posting API v2.
    Returns publish_id.
    """
    p = Path(video_path)
    if not p.exists():
        raise TikTokError(f"Video file not found: {p}")

    file_size = p.stat().st_size
    if file_size < 100:
        raise TikTokError("Video file is empty or corrupted.")

    token = _get_access_token(cfg)
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=UTF-8",
    }

    # Allowed privacy levels: PUBLIC_TO_EVERYONE, MUTUAL_FOLLOW_FRIENDS, SELF_ONLY
    valid_privacy = ("PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS", "SELF_ONLY")
    priv = privacy if privacy in valid_privacy else cfg.get("tiktok_privacy", "PUBLIC_TO_EVERYONE")
    if priv not in valid_privacy:
        priv = "PUBLIC_TO_EVERYONE"

    # Step 1: Initialize Direct Post
    init_payload = {
        "post_info": {
            "title": title[:150] or "Clip from ClipForge",
            "privacy_level": priv,
            "disable_duet": False,
            "disable_stitch": False,
            "disable_comment": False,
            "video_cover_timestamp_ms": 1000,
        },
        "source_info": {
            "source": "FILE_UPLOAD",
            "video_size": file_size,
            "chunk_size": file_size,
            "total_chunk_count": 1,
        },
    }

    r = requests.post(TIKTOK_POST_INIT_URL, headers=headers, json=init_payload, timeout=30)
    res = r.json()
    if r.status_code != 200 or res.get("error", {}).get("code") != "ok":
        err_msg = (res.get("error") or {}).get("message") or r.text[:300]
        raise TikTokError(f"TikTok upload initialization failed: {err_msg}")

    data = res.get("data", {})
    publish_id = data.get("publish_id")
    upload_url = data.get("upload_url")
    if not upload_url or not publish_id:
        raise TikTokError("TikTok did not return a valid upload URL.")

    # Step 2: Upload Video Binary
    progress_cb(0.15)
    with open(p, "rb") as vf:
        content = vf.read()

    upload_headers = {
        "Content-Type": "video/mp4",
        "Content-Range": f"bytes 0-{file_size - 1}/{file_size}",
    }
    put_r = requests.put(upload_url, headers=upload_headers, data=content, timeout=300)
    if put_r.status_code not in (200, 201, 206):
        raise TikTokError(f"TikTok video upload stream failed: HTTP {put_r.status_code} {put_r.text[:200]}")

    progress_cb(0.85)

    # Step 3: Verify Status (polling a few seconds for immediate status)
    for _ in range(5):
        time.sleep(2)
        try:
            status_r = requests.post(TIKTOK_STATUS_URL, headers=headers, json={"publish_id": publish_id}, timeout=15)
            s_data = status_r.json().get("data", {})
            st = s_data.get("status")
            if st == "PUBLISH_COMPLETE":
                progress_cb(1.0)
                return publish_id
            if st == "FAILED":
                fail_reason = s_data.get("fail_reason", "Publish failed on TikTok")
                raise TikTokError(f"TikTok publish failed: {fail_reason}")
        except Exception as e:
            if isinstance(e, TikTokError):
                raise
            break

    progress_cb(1.0)
    return publish_id
