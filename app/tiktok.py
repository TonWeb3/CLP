"""TikTok sign-in (OAuth 2.0) and direct video publishing via TikTok Content Posting API v2 (multi-account).
Supports Sandbox and Production developer apps, Upload to TikTok (Inbox/Draft mode), and Direct Post.
"""
from __future__ import annotations

import json
import logging
import math
import os
import secrets
import threading
import time
from pathlib import Path
from urllib.parse import urlencode

import requests

from . import config, db

log = logging.getLogger("clipforge.tiktok")
TOKEN_FILE = config.DATA_DIR / "tiktok_token.json"
TIKTOK_AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TIKTOK_TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
TIKTOK_POST_INIT_URL = "https://open.tiktokapis.com/v2/post/publish/video/init/"
TIKTOK_INBOX_INIT_URL = "https://open.tiktokapis.com/v2/post/publish/inbox/video/init/"
TIKTOK_STATUS_URL = "https://open.tiktokapis.com/v2/post/publish/status_fetch/"
TIKTOK_CREATOR_INFO_URL = "https://open.tiktokapis.com/v2/post/publish/creator_info/query/"

_states: dict[str, tuple[float, int | None]] = {}
_lock = threading.Lock()
_refresh_lock = threading.Lock()


class TikTokError(RuntimeError):
    pass


def token_path(account_id: int | None = None) -> Path:
    if account_id is not None:
        return config.DATA_DIR / "tokens" / f"tiktok_{int(account_id)}.json"
    return TOKEN_FILE


def _resolve_account(cfg_or_acc: dict | int | None = None) -> dict:
    cfg = config.get()
    if isinstance(cfg_or_acc, int):
        acc = db.one("SELECT * FROM social_accounts WHERE id=? AND platform='tiktok'", (cfg_or_acc,))
        if not acc:
            raise TikTokError("TikTok account not found.")
        acc = dict(acc)
        if not acc.get("client_id"):
            acc["client_id"] = cfg.get("tiktok_client_key", "")
        if not acc.get("client_secret"):
            acc["client_secret"] = cfg.get("tiktok_client_secret", "")
        if not acc.get("privacy"):
            acc["privacy"] = cfg.get("tiktok_privacy", "INBOX")
        return acc
    if isinstance(cfg_or_acc, dict) and ("client_id" in cfg_or_acc or "id" in cfg_or_acc):
        acc = dict(cfg_or_acc)
        if not acc.get("client_id"):
            acc["client_id"] = cfg.get("tiktok_client_key", "")
        if not acc.get("client_secret"):
            acc["client_secret"] = cfg.get("tiktok_client_secret", "")
        if not acc.get("privacy"):
            acc["privacy"] = cfg.get("tiktok_privacy", "INBOX")
        return acc
    rows = db.q("SELECT * FROM social_accounts WHERE platform='tiktok' ORDER BY id ASC")
    for r in rows:
        if token_path(r["id"]).exists():
            acc = dict(r)
            if not acc.get("client_id"):
                acc["client_id"] = cfg.get("tiktok_client_key", "")
            if not acc.get("client_secret"):
                acc["client_secret"] = cfg.get("tiktok_client_secret", "")
            return acc
    if rows:
        acc = dict(rows[0])
        if not acc.get("client_id"):
            acc["client_id"] = cfg.get("tiktok_client_key", "")
        if not acc.get("client_secret"):
            acc["client_secret"] = cfg.get("tiktok_client_secret", "")
        return acc
    return {
        "id": None,
        "name": "TikTok",
        "username": "",
        "scope": "",
        "client_id": cfg.get("tiktok_client_key", ""),
        "client_secret": cfg.get("tiktok_client_secret", ""),
        "privacy": cfg.get("tiktok_privacy", "INBOX"),
    }


def redirect_uri_for(base_url: str) -> str:
    return base_url.rstrip("/") + "/api/tiktok/callback"


def auth_url(cfg_or_acc: dict | int, redirect_uri: str, scope: str | None = None) -> str:
    acc = _resolve_account(cfg_or_acc)
    key = (acc.get("client_id") or acc.get("tiktok_client_key") or "").strip()
    secret = (acc.get("client_secret") or acc.get("tiktok_client_secret") or "").strip()
    if not key or not secret:
        name = acc.get("name") or "TikTok"
        raise TikTokError(f"Add your TikTok Client Key and Client Secret for '{name}' in Settings first.")

    state = secrets.token_urlsafe(24)
    with _lock:
        now = time.time()
        # Clean up stale states (>1 hour)
        expired = [k for k, v in _states.items() if now - (v[0] if isinstance(v, tuple) else v) > 3600]
        for k in expired:
            _states.pop(k, None)
        _states[state] = (now, acc.get("id"))

    # Resolve scopes:
    # In Sandbox apps, "Upload to TikTok" (Inbox mode) uses video.upload.
    # If the user only has Upload to TikTok enabled in the portal, requesting video.publish causes TikTok to reject auth.
    # Therefore, if the account's mode is INBOX, default to video.upload.
    # If direct post is requested, default to video.upload,video.publish.
    chosen_scope = scope or acc.get("scope")
    if not chosen_scope:
        priv = (acc.get("privacy") or "INBOX").upper()
        if priv == "INBOX":
            chosen_scope = "video.upload"
        else:
            chosen_scope = "video.upload,video.publish"

    params = {
        "client_key": key,
        "scope": chosen_scope,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "state": state,
    }
    return f"{TIKTOK_AUTH_URL}?{urlencode(params)}"


def get_creator_info(cfg_or_acc: dict | int) -> dict:
    """Fetch creator profile info (username, nickname, avatar, allowed privacy levels, etc.)"""
    try:
        acc = _resolve_account(cfg_or_acc)
        token = _get_access_token(acc)
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=UTF-8",
        }
        r = requests.post(TIKTOK_CREATOR_INFO_URL, headers=headers, json={}, timeout=15)
        if r.status_code == 200:
            res = r.json()
            if res.get("error", {}).get("code") == "ok":
                return res.get("data", {})
        log.warning("TikTok creator_info query returned %s: %s", r.status_code, r.text[:200])
    except Exception as e:
        log.warning("TikTok creator_info query failed: %s", e)
    return {}


def finish(state: str, code: str, redirect_uri: str) -> int | None:
    with _lock:
        entry = _states.pop(state, None)
    if not entry:
        raise TikTokError("This sign-in link has expired. Start the connection again from Settings.")
    saved_time, account_id = entry if isinstance(entry, tuple) else (entry, None)
    if time.time() - saved_time > 3600:
        raise TikTokError("This sign-in link has expired. Start the connection again from Settings.")

    acc = _resolve_account(account_id)
    key = (acc.get("client_id") or acc.get("tiktok_client_key") or "").strip()
    secret = (acc.get("client_secret") or acc.get("tiktok_client_secret") or "").strip()
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

    if account_id is None:
        cfg = config.get()
        now = time.time()
        account_id = db.insert(
            "social_accounts",
            platform="tiktok",
            name="TikTok Account",
            client_id="",
            client_secret="",
            privacy=cfg.get("tiktok_privacy", "INBOX"),
            add_shorts_tag=0,
            created=now,
            updated=now,
        )

    tpath = token_path(account_id)
    tpath.parent.mkdir(parents=True, exist_ok=True)
    tpath.write_text(json.dumps(token_data, indent=2), "utf-8")
    try:
        os.chmod(tpath, 0o600)
    except OSError:
        pass

    # Try fetching creator profile info to automatically set the username/name
    try:
        info = get_creator_info(account_id)
        uname = info.get("creator_username") or info.get("creator_nickname")
        if uname:
            db.update("social_accounts", account_id, username=uname)
            cur_acc = db.one("SELECT name FROM social_accounts WHERE id=?", (account_id,))
            if cur_acc and ("TikTok" in cur_acc["name"]):
                db.update("social_accounts", account_id, name=f"@{uname}")
    except Exception as e:
        log.warning("Could not auto-fetch TikTok creator username: %s", e)

    return account_id


def connected(account_id: int | None = None) -> bool:
    if account_id is not None:
        return token_path(account_id).exists()
    rows = db.q("SELECT id FROM social_accounts WHERE platform='tiktok'")
    if any(token_path(r["id"]).exists() for r in rows):
        return True
    return TOKEN_FILE.exists()


def disconnect(account_id: int | None = None) -> None:
    if account_id is not None:
        token_path(account_id).unlink(missing_ok=True)
    else:
        TOKEN_FILE.unlink(missing_ok=True)


def _get_access_token(acc: dict) -> str:
    tpath = token_path(acc.get("id"))
    if not tpath.exists() and TOKEN_FILE.exists():
        tpath = TOKEN_FILE
    if not tpath.exists():
        name = acc.get("name") or "TikTok"
        raise TikTokError(f"TikTok account '{name}' is not connected. Connect it in Settings.")

    with _refresh_lock:
        try:
            data = json.loads(tpath.read_text("utf-8"))
        except Exception as e:
            name = acc.get("name") or "TikTok"
            raise TikTokError(f"Failed to read token for '{name}'. Please reconnect in Settings.") from e

        token = data.get("access_token")
        expires_in = data.get("expires_in", 86400)
        created_at = data.get("created_at", 0)

        # If within 5 minutes of expiring, refresh
        if time.time() - created_at > (expires_in - 300) and data.get("refresh_token"):
            key = (acc.get("client_id") or acc.get("tiktok_client_key") or "").strip()
            secret = (acc.get("client_secret") or acc.get("tiktok_client_secret") or "").strip()
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
                tpath.write_text(json.dumps(data, indent=2), "utf-8")
                token = data["access_token"]
            else:
                log.warning("TikTok token refresh failed; trying existing access token")

        if not token:
            name = acc.get("name") or "TikTok"
            raise TikTokError(f"TikTok token for '{name}' expired or missing. Reconnect in Settings.")
        return token


def upload(cfg_or_acc: dict | int, video_path: str | Path, title: str, privacy: str, progress_cb) -> str:
    """Uploads a video to TikTok using the Content Posting API v2.
    Supports both Direct Post and Inbox / Draft mode (required for unaudited / Sandbox apps).
    Handles chunked upload for videos of any size.
    Returns publish_id.
    """
    p = Path(video_path)
    if not p.exists():
        raise TikTokError(f"Video file not found: {p}")

    file_size = p.stat().st_size
    if file_size < 100:
        raise TikTokError("Video file is empty or corrupted.")

    acc = _resolve_account(cfg_or_acc)
    token = _get_access_token(acc)
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=UTF-8",
    }

    # Allowed privacy levels: INBOX (Draft mode), SELF_ONLY, MUTUAL_FOLLOW_FRIENDS, PUBLIC_TO_EVERYONE
    valid_privacy = ("INBOX", "SELF_ONLY", "MUTUAL_FOLLOW_FRIENDS", "PUBLIC_TO_EVERYONE")
    default_priv = (acc.get("privacy") or "INBOX").upper()
    priv = (privacy if privacy in valid_privacy else default_priv).upper()
    if priv not in valid_privacy:
        priv = "INBOX"

    is_inbox = (priv == "INBOX")

    # Step 1: Chunk Calculation
    # TikTok requires chunks between 5MB and 64MB. The last chunk may be smaller.
    MAX_SINGLE_CHUNK = 64 * 1024 * 1024
    if file_size <= MAX_SINGLE_CHUNK:
        chunk_size = file_size
        total_chunks = 1
    else:
        chunk_size = 10 * 1024 * 1024  # 10 MB chunks
        total_chunks = math.ceil(file_size / chunk_size)

    # Step 2: Initialize Direct Post or Inbox Draft
    if is_inbox:
        init_url = TIKTOK_INBOX_INIT_URL
        # In inbox mode, no post_info (title/privacy) is accepted by TikTok
        init_payload = {
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": file_size,
                "chunk_size": chunk_size,
                "total_chunk_count": total_chunks,
            },
        }
    else:
        init_url = TIKTOK_POST_INIT_URL
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
                "chunk_size": chunk_size,
                "total_chunk_count": total_chunks,
            },
        }

    r = requests.post(init_url, headers=headers, json=init_payload, timeout=30)
    res = r.json()
    if r.status_code != 200 or res.get("error", {}).get("code") != "ok":
        err_code = (res.get("error") or {}).get("code", "")
        err_msg = (res.get("error") or {}).get("message") or r.text[:300]
        if "unaudited_client" in err_msg.lower() or "unaudited" in err_code.lower():
            raise TikTokError(
                "Your TikTok app is unaudited and cannot post public videos directly. "
                "Change privacy to 'INBOX' (send to app draft) or 'SELF_ONLY' (private)."
            )
        raise TikTokError(f"TikTok upload initialization failed: {err_msg} ({err_code})")

    data = res.get("data", {})
    publish_id = data.get("publish_id")
    upload_url = data.get("upload_url")
    if not upload_url or not publish_id:
        raise TikTokError("TikTok did not return a valid upload URL.")

    # Step 3: Upload Video Binary (Chunked)
    progress_cb(0.15)
    with open(p, "rb") as vf:
        for chunk_idx in range(total_chunks):
            chunk_data = vf.read(chunk_size)
            if not chunk_data:
                break
            start_byte = chunk_idx * chunk_size
            end_byte = start_byte + len(chunk_data) - 1
            upload_headers = {
                "Content-Type": "video/mp4",
                "Content-Range": f"bytes {start_byte}-{end_byte}/{file_size}",
            }
            put_r = requests.put(upload_url, headers=upload_headers, data=chunk_data, timeout=300)
            if put_r.status_code not in (200, 201, 206):
                raise TikTokError(
                    f"TikTok video upload stream failed on chunk {chunk_idx + 1}/{total_chunks}: "
                    f"HTTP {put_r.status_code} {put_r.text[:200]}"
                )
            progress_cb(0.15 + 0.65 * ((chunk_idx + 1) / total_chunks))

    # Step 4: Verify Status
    # In inbox mode: SEND_TO_USER_INBOX indicates the draft was delivered to the user's TikTok app inbox.
    # In direct post mode: PUBLISH_COMPLETE indicates the post is live.
    progress_cb(0.85)
    for attempt in range(12):
        time.sleep(2)
        try:
            status_r = requests.post(TIKTOK_STATUS_URL, headers=headers, json={"publish_id": publish_id}, timeout=15)
            s_data = status_r.json().get("data", {})
            st = s_data.get("status")
            if is_inbox and st in ("SEND_TO_USER_INBOX", "PUBLISH_COMPLETE"):
                progress_cb(1.0)
                return publish_id
            if not is_inbox and st == "PUBLISH_COMPLETE":
                progress_cb(1.0)
                return publish_id
            if st == "FAILED":
                fail_reason = s_data.get("fail_reason", "Publish failed on TikTok")
                if "unaudited" in fail_reason.lower():
                    fail_reason += " (Use 'INBOX' draft mode for unaudited / sandbox apps)"
                raise TikTokError(f"TikTok publish failed: {fail_reason}")
            progress_cb(min(0.98, 0.85 + 0.01 * (attempt + 1)))
        except TikTokError:
            raise
        except Exception as e:
            log.warning("TikTok status check attempt %d failed: %s", attempt, e)

    progress_cb(1.0)
    return publish_id
