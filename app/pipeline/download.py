"""Download a video with yt-dlp."""
from __future__ import annotations

import glob
from pathlib import Path


class DownloadError(RuntimeError):
    pass


def _friendly(msg: str) -> str:
    low = msg.lower()
    if "sign in to confirm" in low or "not a bot" in low:
        return ("YouTube is asking this server to prove it is not a bot. Open Settings, paste a cookies.txt "
                "exported from a logged-in browser into the Cookies box, and retry.")
    if "private video" in low:
        return "This video is private."
    if "video unavailable" in low or "unavailable" in low and "video" in low:
        return "This video is unavailable."
    if "ffmpeg" in low and "not found" in low:
        return "ffmpeg is not installed on this server."
    return msg[-400:]


def download(url: str, dest_dir: Path, cfg: dict, progress_cb, cancel_check) -> dict:
    """Returns {"path", "title", "duration"}. progress_cb(fraction, message)."""
    try:
        import yt_dlp
    except ImportError as e:  # pragma: no cover
        raise DownloadError("yt-dlp is not installed. Run: pip install yt-dlp") from e

    dest_dir.mkdir(parents=True, exist_ok=True)
    h = int(cfg.get("max_height") or 1080)
    opts = {
        "format": (f"bv*[height<={h}][ext=mp4]+ba[ext=m4a]/b[height<={h}][ext=mp4]/"
                   f"bv*[height<={h}]+ba/b[height<={h}]/b"),
        "merge_output_format": "mp4",
        "outtmpl": str(dest_dir / "source.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "retries": 5,
        "fragment_retries": 5,
        "restrictfilenames": True,
        "concurrent_fragment_downloads": 4,
    }
    cookies = (cfg.get("cookies") or "").strip()
    if cookies:
        cf = dest_dir / "cookies.txt"
        cf.write_text(cookies + "\n", "utf-8")
        opts["cookiefile"] = str(cf)

    def hook(d):
        cancel_check()
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done = d.get("downloaded_bytes") or 0
            frac = done / total if total else 0.0
            speed = d.get("speed")
            msg = "Downloading video"
            if speed:
                msg += f" ({speed / 1e6:.1f} MB/s)"
            progress_cb(min(frac, 0.99), msg)
        elif d.get("status") == "finished":
            progress_cb(0.99, "Merging audio and video")

    opts["progress_hooks"] = [hook]
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except Exception as e:  # noqa: BLE001
        raise DownloadError(_friendly(str(e))) from e

    path = None
    for rd in (info.get("requested_downloads") or []):
        if rd.get("filepath"):
            path = rd["filepath"]
    if not path or not Path(path).exists():
        cands = sorted(glob.glob(str(dest_dir / "source.*")))
        cands = [c for c in cands if not c.endswith((".part", ".ytdl", ".txt"))]
        path = cands[0] if cands else None
    if not path:
        raise DownloadError("Download finished but the video file was not found.")
    return {"path": path, "title": info.get("title") or "Untitled", "duration": float(info.get("duration") or 0)}
