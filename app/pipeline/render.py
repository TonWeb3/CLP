"""Cut a clip, reframe it, burn in captions, and encode it with ffmpeg."""
from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path

from .. import config
from . import media

ASPECTS = {"9:16": (1080, 1920), "1:1": (1080, 1080), "4:5": (1080, 1350), "16:9": (1920, 1080)}


def target_size(aspect: str, out_width: int) -> tuple[int, int]:
    w, h = ASPECTS.get(aspect, ASPECTS["9:16"])
    if aspect == "16:9":
        w = int(round(out_width * 16 / 9 / 2) * 2)
        return w, int(round(w * 9 / 16 / 2) * 2)
    scale = out_width / 1080
    return int(round(w * scale / 2) * 2), int(round(h * scale / 2) * 2)


# --------------------------------------------------------------------------
# Captions (ASS)
# --------------------------------------------------------------------------
def _ass_color(hex_color: str, alpha: int = 0) -> str:
    h = hex_color.lstrip("#")
    if len(h) != 6:
        h = "FFFFFF"
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def _ass_time(t: float) -> str:
    t = max(0.0, t)
    cs = int(round(t * 100))
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _clean(text: str) -> str:
    return text.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def build_ass(words: list[dict], clip_start: float, clip_end: float, W: int, H: int, cfg: dict) -> str:
    dur = clip_end - clip_start
    size = int(cfg["caption_size"] * W / 1080)
    outline = max(2, int(size * 0.075))
    margin_v = int(H * cfg["caption_position"] / 100)
    margin_h = int(W * 0.07)
    primary = _ass_color(cfg["caption_color"])
    hi = _ass_color(cfg["caption_highlight"])
    out = _ass_color(cfg["caption_outline"])
    header = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {W}\nPlayResY: {H}\nWrapStyle: 0\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,"
        "StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\n"
        f"Style: Default,{cfg['caption_font']},{size},{primary},{primary},{out},&H64000000,-1,0,0,0,100,100,0,0,1,"
        f"{outline},0,2,{margin_h},{margin_h},{margin_v},1\n\n"
        "[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n"
    )
    ws = [w for w in words if w["e"] > clip_start and w["s"] < clip_end]
    for w in ws:
        w["_s"] = max(0.0, w["s"] - clip_start)
        w["_e"] = min(dur, w["e"] - clip_start)

    # group into short lines
    groups: list[list[dict]] = []
    cur: list[dict] = []
    per = int(cfg["caption_words"])
    for i, w in enumerate(ws):
        cur.append(w)
        nxt_gap = (ws[i + 1]["_s"] - w["_e"]) if i + 1 < len(ws) else 99
        sentence_end = w["w"].endswith((".", "?", "!", "…"))
        if len(cur) >= per or nxt_gap > 0.7 or sentence_end:
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)

    upper = bool(cfg["caption_upper"])
    events = []
    for gi, g in enumerate(groups):
        texts = [(_clean(w["w"]).upper() if upper else _clean(w["w"])) for w in g]
        group_end_cap = groups[gi + 1][0]["_s"] if gi + 1 < len(groups) else dur
        for k, w in enumerate(g):
            start = w["_s"]
            if k + 1 < len(g):
                end = g[k + 1]["_s"]
            else:
                end = min(group_end_cap, w["_e"] + 0.25)
            if end <= start:
                end = start + 0.08
            parts = []
            for j, t in enumerate(texts):
                if j == k:
                    parts.append(f"{{\\1c{hi}\\fscx112\\fscy112}}{t}{{\\1c{primary}\\fscx100\\fscy100}}")
                else:
                    parts.append(t)
            events.append(f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Default,,0,0,0,,{' '.join(parts)}")
    return header + "\n".join(events) + "\n"


# --------------------------------------------------------------------------
# Face tracking
# --------------------------------------------------------------------------
def _rdp(points: list[tuple[float, float]], eps: float) -> list[tuple[float, float]]:
    if len(points) < 3:
        return points
    (x1, y1), (x2, y2) = points[0], points[-1]
    best, idx = 0.0, 0
    for i in range(1, len(points) - 1):
        x0, y0 = points[i]
        # vertical distance to the line (we simplify a function of time)
        t = (x0 - x1) / (x2 - x1) if x2 != x1 else 0
        d = abs(y0 - (y1 + t * (y2 - y1)))
        if d > best:
            best, idx = d, i
    if best > eps:
        return _rdp(points[: idx + 1], eps)[:-1] + _rdp(points[idx:], eps)
    return [points[0], points[-1]]


def face_path(src: str, start: float, end: float, src_w: int, src_h: int, crop_w: int,
              samples_per_sec: float = 4.0) -> list[tuple[float, float]] | None:
    """Return [(t, crop_x)] with t relative to the clip start, or None if no face was found."""
    try:
        import cv2
    except ImportError:
        return None
    cascade_path = getattr(getattr(cv2, "data", None), "haarcascades", "") + "haarcascade_frontalface_default.xml"
    cascade = cv2.CascadeClassifier(cascade_path)
    if cascade.empty():
        return None
    cap = cv2.VideoCapture(src)
    if not cap.isOpened():
        return None
    det_w = 640
    scale = det_w / src_w if src_w > det_w else 1.0
    raw: list[tuple[float, float | None]] = []
    prev_cx: float | None = None
    t = start
    step = 1.0 / samples_per_sec
    try:
        while t < end:
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ok, frame = cap.read()
            if not ok:
                break
            small = cv2.resize(frame, None, fx=scale, fy=scale) if scale != 1.0 else frame
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=6,
                                             minSize=(max(30, int(40 * scale)), max(30, int(40 * scale))))
            cx = None
            if len(faces):
                cands = [((x + w / 2) / scale, float(w * h)) for (x, y, w, h) in faces]
                # Stay with the face we were already following; Haar often reports a second, larger
                # false box next to a real face, so continuity beats size. Jump to the biggest face
                # only when the followed one is gone.
                near = [c for c in cands if prev_cx is not None and abs(c[0] - prev_cx) < crop_w * 0.45]
                cx = min(near, key=lambda c: abs(c[0] - prev_cx))[0] if near else max(cands, key=lambda c: c[1])[0]
                prev_cx = cx
            raw.append((t - start, cx))
            t += step
    finally:
        cap.release()

    valid = [(tt, c) for tt, c in raw if c is not None]
    if len(valid) < max(3, len(raw) * 0.15):
        return None

    # fill gaps by holding the last known position (and back-fill the start)
    filled: list[float] = []
    last = valid[0][1]
    for _tt, c in raw:
        if c is not None:
            last = c
        filled.append(last)

    # median smoothing
    sm = []
    for i in range(len(filled)):
        win = sorted(filled[max(0, i - 2): i + 3])
        sm.append(win[len(win) // 2])

    # dead zone: the camera only moves when the subject leaves the middle of the frame
    dead = crop_w * 0.06
    max_speed = crop_w * 0.9  # px per second
    cam = sm[0]
    path = []
    for (tt, _), target in zip(raw, sm):
        diff = target - cam
        if abs(diff) > dead:
            move = abs(diff) - dead
            move = min(move, max_speed * step)
            cam += move if diff > 0 else -move
        path.append((tt, cam))

    # convert subject centre to crop x and simplify
    half = crop_w / 2
    pts = [(tt, min(max(c - half, 0), src_w - crop_w)) for tt, c in path]
    eps = 4.0
    simp = _rdp(pts, eps)
    while len(simp) > 40:
        eps *= 1.6
        simp = _rdp(pts, eps)
    return simp


def _lerp_expr(points: list[tuple[float, float]]) -> str:
    if len(points) == 1:
        return f"{points[0][1]:.1f}"
    expr = f"{points[-1][1]:.1f}"
    for (t0, x0), (t1, x1) in reversed(list(zip(points[:-1], points[1:]))):
        if t1 - t0 < 1e-3:
            continue
        seg = f"{x0:.1f}+({x1 - x0:.1f})*(t-{t0:.3f})/{t1 - t0:.3f}"
        expr = f"if(lt(t,{t1:.3f}),{seg},{expr})"
    return expr


# --------------------------------------------------------------------------
# Filter graph
# --------------------------------------------------------------------------
def _esc(path: str) -> str:
    return re.sub(r"([\\:',\[\];])", r"\\\1", path)


def _subs_filter(has_subs: bool, in_label: str, out_label: str) -> str:
    if not has_subs:
        return f"[{in_label}]null[{out_label}]"
    fonts = config.DATA_DIR / "fonts"
    opt = ""
    if fonts.is_dir() and any(fonts.iterdir()):
        opt = f":fontsdir={_esc(str(fonts))}"
    return f"[{in_label}]ass=subs.ass{opt}[{out_label}]"


def build_graph(mode: str, aspect: str, src_w: int, src_h: int, tw: int, th: int,
                path: list[tuple[float, float]] | None, crop_w: int, has_subs: bool) -> str:
    """Return a -filter_complex graph taking [0:v] and producing [v]."""
    if aspect == "16:9":
        base = (f"[0:v]scale={tw}:{th}:force_original_aspect_ratio=decrease,"
                f"pad={tw}:{th}:(ow-iw)/2:(oh-ih)/2,setsar=1[v0]")
    elif mode == "blur" or crop_w > src_w:
        base = (f"[0:v]split=2[a][b];"
                f"[a]scale={tw}:{th}:force_original_aspect_ratio=increase,crop={tw}:{th},boxblur=28:3[bg];"
                f"[b]scale={tw}:{th}:force_original_aspect_ratio=decrease[fg];"
                f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[v0]")
    else:
        cw = crop_w - (crop_w % 2)
        ch = src_h - (src_h % 2)
        if mode == "face" and path:
            x = _lerp_expr(path)
        else:
            x = f"{(src_w - cw) / 2:.1f}"
        base = f"[0:v]crop={cw}:{ch}:x='{x}':y=0,scale={tw}:{th}:flags=lanczos,setsar=1[v0]"
    return base + ";" + _subs_filter(has_subs, "v0", "v")


def _slug(text: str, n: int = 48) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    return (s[:n].strip("-")) or "clip"


def render(clip: dict, video: dict, words: list[dict], opts: dict, cfg: dict, progress_cb) -> dict:
    """Render one clip. Returns {path, thumb, width, height, size}."""
    src = video["path"]
    info = media.probe(src)
    sw, sh = info["width"], info["height"]
    if not info["has_video"]:
        raise RuntimeError("The source has no video stream.")
    start = max(0.0, float(clip["start"]))
    end = min(float(clip["end"]), info["duration"] or float(clip["end"]))
    dur = end - start
    if dur < 1:
        raise RuntimeError("The clip is shorter than one second.")

    aspect = opts.get("aspect") or cfg["aspect"]
    mode = opts.get("reframe") or cfg["reframe"]
    captions = bool(opts.get("captions", cfg["captions"]))
    tw, th = target_size(aspect, int(cfg["output_width"]))
    crop_w = int(round(sh * tw / th))
    crop_w -= crop_w % 2

    progress_cb(0.02, "Preparing")
    path = None
    if aspect != "16:9" and mode == "face" and crop_w <= sw:
        progress_cb(0.04, "Tracking faces")
        path = face_path(src, start, end, sw, sh, crop_w)
        if path is None:
            mode = "center"  # no face found, keep the middle of the frame

    has_subs = captions and any(w["e"] > start and w["s"] < end for w in words)
    out_dir = config.clips_dir() / _slug(video.get("title") or f"video-{video['id']}", 40)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{clip['id']:04d}-{_slug(clip.get('title') or 'clip')}.mp4"
    tmp_out = out_path.with_suffix(".part.mp4")

    enc = media.pick_encoder(cfg.get("encoder", "auto"))
    q = int(cfg["quality"])
    if enc == "h264_nvenc":
        venc = ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", str(q + 3), "-b:v", "0"]
    else:
        venc = ["-c:v", "libx264", "-preset", "veryfast", "-crf", str(q)]

    with tempfile.TemporaryDirectory(prefix="clipforge-") as td:
        if has_subs:
            (Path(td) / "subs.ass").write_text(build_ass([dict(w) for w in words], start, end, tw, th, cfg), "utf-8")
        graph = build_graph(mode, aspect, sw, sh, tw, th, path, crop_w, has_subs)
        cmd = [media.FFMPEG, "-y", "-v", "error", "-nostats", "-progress", "pipe:1",
               "-ss", f"{start:.3f}", "-i", src, "-t", f"{dur:.3f}",
               "-filter_complex", graph, "-map", "[v]", "-map", "0:a:0?",
               *venc, "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-ar", "44100",
               "-movflags", "+faststart", str(tmp_out)]
        log_path = Path(td) / "ffmpeg.log"
        with open(log_path, "wb") as logf:
            proc = subprocess.Popen(cmd, cwd=td, stdout=subprocess.PIPE, stderr=logf, text=True)
            assert proc.stdout is not None
            for line in proc.stdout:
                if line.startswith("out_time_ms="):
                    try:
                        sec = int(line.split("=")[1]) / 1_000_000
                        progress_cb(0.1 + 0.88 * min(sec / dur, 1.0), "Rendering")
                    except ValueError:
                        pass
            code = proc.wait()
        if code != 0:
            err = log_path.read_text("utf-8", "replace")[-600:]
            tmp_out.unlink(missing_ok=True)
            raise RuntimeError("ffmpeg failed: " + err.strip())

    tmp_out.replace(out_path)
    thumb = out_path.with_suffix(".jpg")
    media.make_thumb(out_path, thumb, at=min(1.0, dur / 2), width=360)
    return {
        "path": str(out_path),
        "thumb": str(thumb) if thumb.exists() else None,
        "width": tw, "height": th,
        "size": out_path.stat().st_size,
        "mode": mode,
    }
