"""YouTube sign-in (OAuth) and resumable upload (supports multiple named accounts)."""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from . import config, db

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
TOKEN_FILE = config.DATA_DIR / "youtube_token.json"
_flows: dict[str, tuple[object, int | None]] = {}
_lock = threading.Lock()


class YouTubeError(RuntimeError):
    pass


def token_path(account_id: int | None = None) -> Path:
    if account_id is not None:
        return config.DATA_DIR / "tokens" / f"youtube_{int(account_id)}.json"
    return TOKEN_FILE


def _resolve_account(cfg_or_acc: dict | int | None = None) -> dict:
    if isinstance(cfg_or_acc, int):
        acc = db.one("SELECT * FROM social_accounts WHERE id=? AND platform='youtube'", (cfg_or_acc,))
        if not acc:
            raise YouTubeError("YouTube account not found.")
        return acc
    if isinstance(cfg_or_acc, dict) and ("client_id" in cfg_or_acc or "id" in cfg_or_acc):
        return cfg_or_acc
    rows = db.q("SELECT * FROM social_accounts WHERE platform='youtube' ORDER BY id ASC")
    for r in rows:
        if token_path(r["id"]).exists():
            return r
    if rows:
        return rows[0]
    cfg = cfg_or_acc if isinstance(cfg_or_acc, dict) else config.get()
    return {
        "id": None,
        "name": "YouTube",
        "client_id": cfg.get("yt_client_id", ""),
        "client_secret": cfg.get("yt_client_secret", ""),
        "privacy": cfg.get("yt_privacy", "private"),
        "add_shorts_tag": cfg.get("yt_add_shorts_tag", True),
    }


def _client_config(acc: dict, redirect_uri: str) -> dict:
    cid = (acc.get("client_id") or acc.get("yt_client_id") or "").strip()
    secret = (acc.get("client_secret") or acc.get("yt_client_secret") or "").strip()
    if not cid or not secret:
        name = acc.get("name") or "YouTube"
        raise YouTubeError(f"Add your Google OAuth Client ID and Secret for '{name}' in Settings first.")
    return {"web": {
        "client_id": cid,
        "client_secret": secret,
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": [redirect_uri],
    }}


def redirect_uri_for(base_url: str) -> str:
    return base_url.rstrip("/") + "/api/youtube/callback"


def auth_url(cfg_or_acc: dict | int, redirect_uri: str) -> str:
    from google_auth_oauthlib.flow import Flow
    acc = _resolve_account(cfg_or_acc)
    flow = Flow.from_client_config(_client_config(acc, redirect_uri), scopes=SCOPES, redirect_uri=redirect_uri)
    url, state = flow.authorization_url(access_type="offline", prompt="consent", include_granted_scopes="true")
    with _lock:
        _flows[state] = (flow, acc.get("id"))
    return url


def finish(state: str, code: str) -> int | None:
    os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")  # callback is http://localhost
    os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")
    with _lock:
        entry = _flows.pop(state, None)
    if entry is None:
        raise YouTubeError("This sign-in link expired. Start the connection again from Settings.")
    flow, account_id = entry if isinstance(entry, tuple) else (entry, None)
    flow.fetch_token(code=code)
    tpath = token_path(account_id)
    tpath.parent.mkdir(parents=True, exist_ok=True)
    tpath.write_text(flow.credentials.to_json(), "utf-8")
    try:
        os.chmod(tpath, 0o600)
    except OSError:
        pass
    return account_id


def connected(account_id: int | None = None) -> bool:
    if account_id is not None:
        return token_path(account_id).exists()
    rows = db.q("SELECT id FROM social_accounts WHERE platform='youtube'")
    if any(token_path(r["id"]).exists() for r in rows):
        return True
    return TOKEN_FILE.exists()


def disconnect(account_id: int | None = None) -> None:
    if account_id is not None:
        token_path(account_id).unlink(missing_ok=True)
    else:
        TOKEN_FILE.unlink(missing_ok=True)


def _credentials(acc: dict):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    tpath = token_path(acc.get("id"))
    if not tpath.exists() and TOKEN_FILE.exists():
        tpath = TOKEN_FILE
    if not tpath.exists():
        name = acc.get("name") or "YouTube"
        raise YouTubeError(f"YouTube account '{name}' is not connected. Connect it in Settings.")
    data = json.loads(tpath.read_text("utf-8"))
    # Ensure client_id / client_secret from the account record are present if needed for refresh
    if acc.get("client_id"):
        data["client_id"] = acc["client_id"].strip()
    if acc.get("client_secret"):
        data["client_secret"] = acc["client_secret"].strip()
    creds = Credentials.from_authorized_user_info(data, SCOPES)
    if not creds.valid:
        if creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:  # noqa: BLE001
                name = acc.get("name") or "YouTube"
                raise YouTubeError(f"YouTube access for '{name}' expired. Reconnect it in Settings.") from e
            tpath.write_text(creds.to_json(), "utf-8")
        else:
            name = acc.get("name") or "YouTube"
            raise YouTubeError(f"YouTube access for '{name}' expired. Reconnect it in Settings.")
    return creds


def upload(cfg_or_acc: dict | int, path: str, title: str, description: str, tags: list[str], privacy: str, progress_cb) -> str:
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    acc = _resolve_account(cfg_or_acc)
    creds = _credentials(acc)
    yt = build("youtube", "v3", credentials=creds, cache_discovery=False)
    default_priv = acc.get("privacy") or "private"
    priv = privacy if privacy in ("private", "unlisted", "public") else (default_priv if default_priv in ("private", "unlisted", "public") else "private")
    body = {
        "snippet": {
            "title": title[:100] or "Untitled clip",
            "description": description[:4900],
            "tags": [t for t in tags if t][:15],
            "categoryId": "22",
        },
        "status": {"privacyStatus": priv,
                   "selfDeclaredMadeForKids": False},
    }
    media = MediaFileUpload(path, mimetype="video/mp4", chunksize=8 * 1024 * 1024, resumable=True)
    req = yt.videos().insert(part="snippet,status", body=body, media_body=media)
    try:
        response = None
        while response is None:
            status, response = req.next_chunk()
            if status:
                progress_cb(status.progress())
        return response["id"]
    except HttpError as e:
        raise YouTubeError(_explain(e)) from e


def _explain(e) -> str:
    try:
        err = json.loads(e.content.decode("utf-8"))["error"]
        reason = (err.get("errors") or [{}])[0].get("reason", "")
        msg = err.get("message", "")
    except Exception:  # noqa: BLE001
        return f"YouTube error {getattr(e, 'status_code', '')}"
    hints = {
        "quotaExceeded": "Daily YouTube API quota used up. It resets at midnight Pacific time. Each upload costs 1,600 of the default 10,000 daily units.",
        "uploadLimitExceeded": "This channel hit its daily upload limit. Try again tomorrow.",
        "forbidden": "YouTube refused the upload. The signed-in account may not have a channel.",
        "youtubeSignupRequired": "The signed-in Google account has no YouTube channel. Create one first.",
        "accessNotConfigured": "The YouTube Data API v3 is not enabled for your Google Cloud project. Enable it and retry.",
    }
    return hints.get(reason) or f"{msg or 'YouTube error'} ({reason})"
