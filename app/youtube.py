"""YouTube sign-in (OAuth) and resumable upload."""
from __future__ import annotations

import json
import os
import threading

from . import config

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
TOKEN_FILE = config.DATA_DIR / "youtube_token.json"
_flows: dict[str, object] = {}
_lock = threading.Lock()


class YouTubeError(RuntimeError):
    pass


def _client_config(cfg: dict, redirect_uri: str) -> dict:
    cid, secret = cfg.get("yt_client_id", "").strip(), cfg.get("yt_client_secret", "").strip()
    if not cid or not secret:
        raise YouTubeError("Add your Google OAuth client ID and secret in Settings first.")
    return {"web": {
        "client_id": cid,
        "client_secret": secret,
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": [redirect_uri],
    }}


def redirect_uri_for(base_url: str) -> str:
    return base_url.rstrip("/") + "/api/youtube/callback"


def auth_url(cfg: dict, redirect_uri: str) -> str:
    from google_auth_oauthlib.flow import Flow
    flow = Flow.from_client_config(_client_config(cfg, redirect_uri), scopes=SCOPES, redirect_uri=redirect_uri)
    url, state = flow.authorization_url(access_type="offline", prompt="consent", include_granted_scopes="true")
    with _lock:
        _flows[state] = flow
    return url


def finish(state: str, code: str) -> None:
    os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")  # callback is http://localhost
    os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")
    with _lock:
        flow = _flows.pop(state, None)
    if flow is None:
        raise YouTubeError("This sign-in link expired. Start the connection again from Settings.")
    flow.fetch_token(code=code)
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(flow.credentials.to_json(), "utf-8")
    try:
        os.chmod(TOKEN_FILE, 0o600)
    except OSError:
        pass


def connected() -> bool:
    return TOKEN_FILE.exists()


def disconnect() -> None:
    TOKEN_FILE.unlink(missing_ok=True)


def _credentials(cfg: dict):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    if not TOKEN_FILE.exists():
        raise YouTubeError("YouTube is not connected. Connect it in Settings.")
    data = json.loads(TOKEN_FILE.read_text("utf-8"))
    creds = Credentials.from_authorized_user_info(data, SCOPES)
    if not creds.valid:
        if creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:  # noqa: BLE001
                raise YouTubeError("YouTube access expired. Reconnect it in Settings.") from e
            TOKEN_FILE.write_text(creds.to_json(), "utf-8")
        else:
            raise YouTubeError("YouTube access expired. Reconnect it in Settings.")
    return creds


def upload(cfg: dict, path: str, title: str, description: str, tags: list[str], privacy: str, progress_cb) -> str:
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    creds = _credentials(cfg)
    yt = build("youtube", "v3", credentials=creds, cache_discovery=False)
    body = {
        "snippet": {
            "title": title[:100] or "Untitled clip",
            "description": description[:4900],
            "tags": [t for t in tags if t][:15],
            "categoryId": "22",
        },
        "status": {"privacyStatus": privacy if privacy in ("private", "unlisted", "public") else "private",
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
