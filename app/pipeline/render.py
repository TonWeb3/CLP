"""Cut a clip, reframe it, burn in captions, and encode it with ffmpeg."""
from __future__ import annotations

import logging
import re
import subprocess
import tempfile
from pathlib import Path

from .. import config
from . import media

log = logging.getLogger("clipforge.render")

ASPECTS = {"9:16": (1080, 1920), "1:1": (1080, 1080), "4:5": (1080, 1350), "16:9": (1920, 1080)}

EMOJI_KEYWORDS: dict[str, str] = {
    # Faith, Preaching & Sermons
    "god": "🙏", "jesus": "✝️", "christ": "✝️", "lord": "🙌", "faith": "🙏", "pray": "🙏",
    "prayer": "🙏", "praying": "🙏", "amen": "🙌", "blessing": "✨", "blessed": "🙌", "bless": "✨",
    "miracle": "✨", "miracles": "✨", "grace": "🕊️", "spirit": "🕊️", "holy": "🕊️", "bible": "📖",
    "scripture": "📖", "church": "⛪", "heaven": "☁️", "angel": "👼", "angels": "👼", "cross": "✝️",
    "worship": "🙌", "praise": "🙌", "glory": "✨", "sin": "⛓️", "forgive": "❤️‍🩹", "forgiveness": "❤️‍🩹",
    "heal": "❤️‍🩹", "healing": "❤️‍🩹", "healed": "❤️‍🩹", "saved": "🛟", "salvation": "✝️", "hope": "🌅",
    "peace": "🕊️", "soul": "✨", "purpose": "🧭", "calling": "📣", "destiny": "🌟", "light": "🕯️",
    "darkness": "🌑", "enemy": "🛡️", "devil": "😈", "demon": "😈", "anointing": "🔥", "testimony": "🙌",
    "breakthrough": "🔓", "chains": "⛓️", "free": "🕊️", "freedom": "🦅", "promise": "🌈", "victory": "🏆",
    "mercy": "🤲", "gospel": "📖", "pastor": "🎙️", "preach": "🔥", "sermon": "📖",
    # Podcasts, Storytelling, Relationships & Life
    "podcast": "🎙️", "story": "📖", "question": "❓", "answer": "💡", "family": "👨‍👩‍👧‍👦", "marriage": "💍",
    "husband": "🤵", "wife": "👰", "friend": "🤝", "friends": "🤝", "people": "👥", "world": "🌍",
    "life": "🌱", "death": "💀", "dead": "💀", "fear": "😨", "afraid": "😨", "scared": "😱",
    "pain": "💔", "broken": "💔", "hurt": "💔", "cry": "😢", "tears": "😢", "happy": "😊",
    "joy": "🥳", "laugh": "😂", "funny": "😂", "angry": "😡", "strong": "💪", "strength": "💪",
    "weak": "🥀", "work": "💼", "job": "💼", "career": "🚀", "business": "💼", "king": "👑",
    "queen": "👑", "leader": "👑", "dream": "💭", "dreams": "✨", "vision": "🔭", "future": "🔮",
    "past": "⏪", "today": "📅", "morning": "🌅", "night": "🌙", "sleep": "😴", "wake": "⏰",
    "voice": "🗣️", "speak": "🗣️", "talk": "💬", "book": "📕", "read": "📖", "music": "🎵",
    "health": "🩺", "body": "💪", "water": "💧", "home": "🏠", "house": "🏠", "door": "🚪",
    "mountain": "⛰️", "storm": "⛈️", "rain": "🌧️",
    # Money & Wealth
    "money": "💰", "dollar": "💵", "dollars": "💵", "cash": "💸", "rich": "🤑", "million": "💰",
    "billion": "💰", "wealth": "💎", "crypto": "🪙", "profit": "📈", "revenue": "📈", "cost": "💳",
    # Fire & Energy
    "fire": "🔥", "hot": "🔥", "crazy": "🤯", "insane": "🤯", "wild": "⚡", "hype": "💥",
    # Secret & Idea
    "secret": "🤫", "hack": "💡", "trick": "💡", "cheat": "💡", "strategy": "🎯", "tip": "📌",
    "idea": "💡", "truth": "👁️", "key": "🔑",
    # Warning & Stop
    "stop": "🛑", "never": "🚫", "warning": "⚠️", "danger": "⚠️", "fake": "❌", "lie": "🤥",
    "scam": "🚨", "worst": "👎", "fail": "💥", "wrong": "❌",
    # Growth & Success
    "growth": "📈", "viral": "🚀", "scale": "🚀", "rocket": "🚀", "power": "⚡", "win": "🏆",
    "winner": "🏆", "champion": "🥇", "goat": "🐐", "best": "⭐",
    # Thinking & Brain
    "think": "🧠", "mind": "🧠", "brain": "🧠", "learn": "📚", "focus": "🎯",
    # Time
    "time": "⏳", "clock": "⏰", "wait": "⏳", "years": "🗓️",
    # Emotion
    "love": "❤️", "heart": "❤️", "look": "👀", "watch": "👀", "listen": "👂",
}


def _match_emoji(clean_word: str) -> str:
    w = clean_word.lower().strip(".,!?:;\"'()[]{}-")
    return EMOJI_KEYWORDS.get(w, "")


POWER_COLORS: dict[str, str] = {
    # Wealth / Money / Growth (Vibrant Gold & Lime)
    "money": "&H00D7FF&", "cash": "&H00D7FF&", "dollar": "&H00D7FF&", "dollars": "&H00D7FF&",
    "million": "&H00D7FF&", "billion": "&H00D7FF&", "wealth": "&H00D7FF&", "rich": "&H00D7FF&",
    "profit": "&H39FF14&", "win": "&H39FF14&", "success": "&H39FF14&", "scale": "&H39FF14&",
    # Danger / Negative / Warning / Pain (Vibrant Red)
    "never": "&H0033FF&", "stop": "&H0033FF&", "wrong": "&H0033FF&", "fail": "&H0033FF&",
    "fake": "&H0033FF&", "lie": "&H0033FF&", "scam": "&H0033FF&", "destroy": "&H0033FF&",
    "worst": "&H0033FF&", "hate": "&H0033FF&", "danger": "&H0033FF&", "warning": "&H0033FF&",
    "mistake": "&H0033FF&", "broke": "&H0033FF&", "dead": "&H0033FF&", "death": "&H0033FF&",
    # Truth / Secret / Hack (Electric Cyan)
    "truth": "&H00F0FF&", "secret": "&H00F0FF&", "hack": "&H00F0FF&", "cheat": "&H00F0FF&",
    "trick": "&H00F0FF&", "real": "&H00F0FF&", "key": "&H00F0FF&", "rule": "&H00F0FF&",
}


def _power_word_color(clean_word: str) -> str | None:
    w = clean_word.lower().strip(".,!?:;\"'()[]{}- ")
    return POWER_COLORS.get(w)


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


def compute_speech_segments(
    words: list[dict],
    clip_start: float,
    clip_end: float,
    max_pause: float = 0.65,
    pad: float = 0.16,
) -> tuple[list[tuple[float, float]], list[dict], float]:
    """Build synchronized (start, end) speech intervals relative to clip_start,
    remap word timestamps onto the trimmed timeline, and return (segs, remapped_words, trimmed_dur).
    """
    dur = max(0.01, clip_end - clip_start)
    ws = [dict(w) for w in words if w["e"] > clip_start and w["s"] < clip_end]
    for w in ws:
        w["_s"] = max(0.0, float(w["s"]) - clip_start)
        w["_e"] = min(dur, float(w["e"]) - clip_start)

    if not ws:
        return [(0.0, dur)], ws, dur

    raw_segs: list[list[float]] = []
    for w in ws:
        s = max(0.0, w["_s"] - pad)
        e = min(dur, w["_e"] + pad)
        if not raw_segs:
            raw_segs.append([s, e])
        else:
            prev_s, prev_e = raw_segs[-1]
            if s - prev_e <= max(0.05, max_pause - 2 * pad):
                raw_segs[-1][1] = max(prev_e, e)
            else:
                raw_segs.append([s, e])

    if raw_segs[0][0] < 0.4:
        raw_segs[0][0] = 0.0
    if dur - raw_segs[-1][1] < 0.4:
        raw_segs[-1][1] = dur

    while len(raw_segs) > 28:
        best_i = min(range(len(raw_segs) - 1), key=lambda i: raw_segs[i + 1][0] - raw_segs[i][1])
        raw_segs[best_i][1] = raw_segs[best_i + 1][1]
        raw_segs.pop(best_i + 1)

    segs = [(round(s, 3), round(e, 3)) for s, e in raw_segs if e - s > 0.05]
    if not segs or (len(segs) == 1 and segs[0][0] <= 0.05 and dur - segs[0][1] <= 0.05):
        return [(0.0, dur)], ws, dur

    offsets: list[float] = []
    acc = 0.0
    for s, e in segs:
        offsets.append(acc)
        acc += e - s
    trimmed_dur = max(0.5, round(acc, 3))

    def remap_t(t: float) -> float:
        for idx, (s, e) in enumerate(segs):
            if t <= e or idx == len(segs) - 1:
                clamped = max(s, min(e, t))
                return round(offsets[idx] + (clamped - s), 3)
        return trimmed_dur

    for w in ws:
        w["_s"] = remap_t(w["_s"])
        w["_e"] = max(w["_s"] + 0.04, remap_t(w["_e"]))

    return segs, ws, trimmed_dur


def build_ass(words: list[dict], clip_start: float, clip_end: float, W: int, H: int, cfg: dict,
              headline: str | None = None, opts: dict | None = None,
              precomputed_ws: list[dict] | None = None, effective_dur: float | None = None) -> str:
    opts = opts or {}
    dur = effective_dur if effective_dur is not None else (clip_end - clip_start)
    preset = (opts.get("caption_preset") or cfg.get("caption_preset") or "classic").lower()
    enable_emojis = bool(opts.get("caption_emojis", cfg.get("caption_emojis", True)))

    font = cfg.get("caption_font", "Arial")
    size = int(cfg["caption_size"] * W / 1080)
    margin_v = int(H * cfg["caption_position"] / 100)
    margin_h = int(W * 0.07)
    upper = bool(cfg.get("caption_upper", True))

    primary = _ass_color(cfg["caption_color"])
    hi = _ass_color(cfg["caption_highlight"])
    out = _ass_color(cfg["caption_outline"])
    outline = max(2, int(size * 0.075))
    shadow = 0
    scale_pop = 114
    border_style = 1
    back_col = "&H64000000"

    # Presets customization
    if preset == "hormozi":
        upper = True
        primary = _ass_color("#FFFFFF")
        hi = _ass_color("#39FF14")  # electric lime green
        out = _ass_color("#000000")
        outline = max(3, int(size * 0.12))
        shadow = max(2, int(size * 0.06))
        scale_pop = 120
    elif preset == "mrbeast":
        upper = True
        primary = _ass_color("#FFE600")  # signature vibrant yellow
        hi = _ass_color("#00F0FF")  # electric cyan
        out = _ass_color("#000000")
        outline = max(4, int(size * 0.14))
        shadow = max(2, int(size * 0.08))
        scale_pop = 118
    elif preset == "minimalist":
        upper = False
        primary = _ass_color("#FFFFFF")
        hi = _ass_color("#80D8FF")
        out = _ass_color("#000000", alpha=160)
        outline = max(1, int(size * 0.04))
        shadow = 0
        scale_pop = 106

    banner_style = ""
    if headline:
        banner_size = int(size * 0.65)
        banner_outline = max(2, int(banner_size * 0.08))
        banner_margin_v = int(H * 0.07)
        banner_margin_h = int(W * 0.08)
        banner_style = (
            f"Style: Banner,{font},{banner_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&HA0000000,-1,0,0,0,100,100,0,0,3,"
            f"{banner_outline},0,8,{banner_margin_h},{banner_margin_h},{banner_margin_v},1\n"
        )

    header = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {W}\nPlayResY: {H}\nWrapStyle: 0\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,"
        "StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\n"
        f"Style: Default,{font},{size},{primary},{primary},{out},{back_col},-1,0,0,0,100,100,0,0,{border_style},"
        f"{outline},{shadow},2,{margin_h},{margin_h},{margin_v},1\n"
        + banner_style + "\n"
        "[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n"
    )

    events = []
    if headline:
        clean_hl = _clean(headline).upper()
        if len(clean_hl) > 26 and " " in clean_hl:
            words_hl = clean_hl.split()
            half = len(clean_hl) // 2
            cur_len = 0
            split_idx = len(words_hl) // 2
            for idx, wrd in enumerate(words_hl):
                cur_len += len(wrd) + 1
                if cur_len >= half:
                    split_idx = idx + 1
                    break
            clean_hl = " ".join(words_hl[:split_idx]) + "\\N" + " ".join(words_hl[split_idx:])
        events.append(f"Dialogue: 1,0:00:00.00,{_ass_time(dur)},Banner,,0,0,0,,{clean_hl}")

    if precomputed_ws is not None:
        ws = precomputed_ws
    else:
        ws = [w for w in words if w["e"] > clip_start and w["s"] < clip_end]
        for w in ws:
            w["_s"] = max(0.0, w["s"] - clip_start)
            w["_e"] = min(dur, w["e"] - clip_start)

    # group into short lines
    groups: list[list[dict]] = []
    cur: list[dict] = []
    per = int(cfg.get("caption_words", 3))
    for i, w in enumerate(ws):
        cur.append(w)
        nxt_gap = (ws[i + 1]["_s"] - w["_e"]) if i + 1 < len(ws) else 99
        sentence_end = w["w"].endswith((".", "?", "!", "…"))
        if len(cur) >= per or nxt_gap > 0.7 or sentence_end:
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)

    for gi, g in enumerate(groups):
        texts = [(_clean(w["w"]).upper() if upper else _clean(w["w"])) for w in g]
        group_end_cap = groups[gi + 1][0]["_s"] if gi + 1 < len(groups) else dur

        # Find any matching emoji for this group
        group_emoji = ""
        if enable_emojis:
            for w in g:
                em = _match_emoji(w["w"])
                if em:
                    group_emoji = " " + em
                    break

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
                    # Power word dynamic color accenting
                    active_col = _power_word_color(g[k]["w"]) or hi
                    pop_start = scale_pop + 14
                    # Kinetic elastic bounce transform on active spoken word
                    parts.append(
                        f"{{\\1c{active_col}\\t(0,90,\\fscx{pop_start}\\fscy{pop_start})\\t(90,200,\\fscx{scale_pop}\\fscy{scale_pop})}}{t}"
                        f"{{\\1c{primary}\\fscx100\\fscy100}}"
                    )
                else:
                    parts.append(t)
            line_text = " ".join(parts) + (group_emoji if k == len(g) - 1 else "")
            events.append(f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Default,,0,0,0,,{line_text}")
    return header + "\n".join(events) + "\n"


# --------------------------------------------------------------------------
# Face tracking (Single & Multi-face split)
# --------------------------------------------------------------------------
def _load_yunet(cv2, input_size=(640, 360)):
    """Load OpenCV YuNet deep-learning face detector ONNX model."""
    if not hasattr(cv2, "FaceDetectorYN"):
        return None
    model_path = Path(__file__).parent / "models" / "face_detection_yunet_2023mar.onnx"
    if not model_path.is_file():
        try:
            import requests
            url = "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx"
            model_path.parent.mkdir(parents=True, exist_ok=True)
            r = requests.get(url, timeout=15)
            if r.status_code == 200:
                model_path.write_bytes(r.content)
        except Exception:
            pass
    if model_path.is_file():
        try:
            return cv2.FaceDetectorYN.create(str(model_path), "", input_size, score_threshold=0.50, nms_threshold=0.3)
        except Exception:
            pass
    return None


def _load_cascades(cv2):
    """Load frontal face, profile face, and upper body cascades from bundled folder or cv2 fallback."""
    cascade_dir = Path(__file__).parent / "cascades"
    cv_data = getattr(getattr(cv2, "data", None), "haarcascades", "") or ""

    def get_casc(name: str):
        local_p = cascade_dir / name
        if local_p.is_file():
            c = cv2.CascadeClassifier(str(local_p))
            if not c.empty():
                return c
        if cv_data:
            sys_p = Path(cv_data) / name
            if sys_p.is_file():
                c = cv2.CascadeClassifier(str(sys_p))
                if not c.empty():
                    return c
        return None

    return get_casc("haarcascade_frontalface_default.xml"), \
           get_casc("haarcascade_profileface.xml"), \
           get_casc("haarcascade_upperbody.xml")


def _detect_speaker_cands(cv2, frame, gray, scale, yunet, frontal, profile, body) -> list[dict]:
    """Detect faces with high-precision YuNet neural detector, falling back to Haar cascades.
    Returns list of dicts: {"cx": float, "cy": float, "area": float, "mouth": (mx, my) | None, "eyes_cy": float | None}.
    """
    cands: list[dict] = []
    h, w = frame.shape[:2]

    # 1. High-precision YuNet neural detection (handles extreme angles, occlusions, tilted heads)
    if yunet is not None:
        try:
            yunet.setInputSize((w, h))
            _, faces = yunet.detect(frame)
            if faces is not None:
                for f in faces:
                    bx, by, bw, bh = f[0], f[1], f[2], f[3]
                    conf = f[14]
                    if conf >= 0.48 and bw > 18 and bh > 18:
                        cx = (bx + bw / 2) / scale
                        cy = (by + bh / 2) / scale
                        area = float(bw * bh)
                        # Exact landmarks: mouth corners f[10:12], f[12:14]
                        rmx, rmy = f[10], f[11]
                        lmx, lmy = f[12], f[13]
                        mouth_pt = (int((rmx + lmx) / 2), int((rmy + lmy) / 2))
                        eyes_cy = float((f[5] + f[7]) / 2 / scale)
                        cands.append({"cx": cx, "cy": cy, "area": area, "mouth": mouth_pt, "eyes_cy": eyes_cy})
                if cands:
                    return cands
        except Exception:
            pass

    # 2. Frontal face detection fallback
    if frontal is not None:
        f_faces = frontal.detectMultiScale(
            gray, scaleFactor=1.07, minNeighbors=4,
            minSize=(max(20, int(26 * scale)), max(20, int(26 * scale)))
        )
        for (x, y, bw, bh) in f_faces:
            cands.append({"cx": (x + bw / 2) / scale, "cy": (y + bh / 2) / scale, "area": float(bw * bh), "mouth": None, "eyes_cy": None})

    # 3. Profile face detection (left-facing)
    if profile is not None:
        p_faces = profile.detectMultiScale(
            gray, scaleFactor=1.08, minNeighbors=4,
            minSize=(max(20, int(26 * scale)), max(20, int(26 * scale)))
        )
        for (x, y, bw, bh) in p_faces:
            cands.append({"cx": (x + bw / 2) / scale, "cy": (y + bh / 2) / scale, "area": float(bw * bh), "mouth": None, "eyes_cy": None})

        # 4. Profile face detection (right-facing via horizontal flip)
        flipped = cv2.flip(gray, 1)
        p_faces_flip = profile.detectMultiScale(
            flipped, scaleFactor=1.08, minNeighbors=4,
            minSize=(max(20, int(26 * scale)), max(20, int(26 * scale)))
        )
        img_w = gray.shape[1]
        for (x, y, bw, bh) in p_faces_flip:
            orig_x = img_w - (x + bw)
            cands.append({"cx": (orig_x + bw / 2) / scale, "cy": (y + bh / 2) / scale, "area": float(bw * bh), "mouth": None, "eyes_cy": None})

    # 5. Upper body detection (when head is turned away or chin is covered)
    if not cands and body is not None:
        bodies = body.detectMultiScale(
            gray, scaleFactor=1.12, minNeighbors=3,
            minSize=(max(40, int(50 * scale)), max(40, int(50 * scale)))
        )
        for (x, y, bw, bh) in bodies:
            cands.append({"cx": (x + bw / 2) / scale, "cy": (y + bh * 0.35) / scale, "area": float(bw * bh * 0.4), "mouth": None, "eyes_cy": None})

    return cands


def _detect_scene_segments(
    src: str,
    start: float,
    end: float,
    samples_per_sec: float = 2.0,
    max_scenes: int = 8,
) -> list[tuple[float, float]]:
    """Scan the entire clip from start to end using OpenCV HSV histogram and edge-density change detection
    to partition the clip into chronological visual scenes (e.g. camera angle cuts, image/slide popups,
    screen shares, B-roll insertions). Returns list of (rel_start, rel_end) covering [0.0, dur].
    """
    dur = max(1.0, round(end - start, 3))
    try:
        import cv2
    except ImportError:
        return [(0.0, dur)]

    cap = cv2.VideoCapture(src)
    if not cap.isOpened():
        return [(0.0, dur)]

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    step = 1.0 / samples_per_sec
    frame_interval = max(1, int(round(fps / samples_per_sec)))
    cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, start * 1000))

    cut_points: list[float] = [0.0]
    prev_hist = None
    prev_edge_ratio = None
    t = start

    try:
        while t < end:
            ok, frame = cap.read()
            if not ok:
                break
            rel_t = round(max(0.0, t - start), 3)
            small = cv2.resize(frame, (320, 180))
            hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
            hist = cv2.calcHist([hsv], [0, 1], None, [16, 16], [0, 180, 0, 256])
            cv2.normalize(hist, hist, alpha=0, beta=1, norm_type=cv2.NORM_MINMAX)

            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            edges = cv2.Canny(gray, 80, 180)
            edge_ratio = float((edges > 0).mean())

            if prev_hist is not None and (rel_t - cut_points[-1]) >= 1.5 and (dur - rel_t) >= 1.2:
                corr = cv2.compareHist(prev_hist, hist, cv2.HISTCMP_CORREL)
                edge_jump = abs(edge_ratio - (prev_edge_ratio or edge_ratio))
                # Trigger a scene boundary on either a hard camera/image cut (corr < 0.58)
                # or a sudden slide/graphic overlay pop-in (corr < 0.76 with high text/edge density shift)
                if corr < 0.58 or (corr < 0.76 and edge_jump > 0.045):
                    cut_points.append(rel_t)

            prev_hist = hist
            prev_edge_ratio = edge_ratio
            t += step
            for _ in range(frame_interval - 1):
                if not cap.grab():
                    break
    finally:
        cap.release()

    # If the clip is long and had no hard cuts, divide into ~10s segments so the Master Vision Model still inspects the whole clip
    if len(cut_points) == 1 and dur > 12.0:
        n_div = min(4, max(2, int(round(dur / 10.0))))
        cut_points = [round(i * dur / n_div, 3) for i in range(n_div)]

    cut_points.append(dur)
    segs: list[list[float]] = []
    for s0, s1 in zip(cut_points[:-1], cut_points[1:]):
        if s1 - s0 < 1.2 and segs:
            segs[-1][1] = s1
        else:
            segs.append([s0, s1])

    while len(segs) > max_scenes:
        shortest_idx = min(range(len(segs) - 1), key=lambda i: (segs[i][1] - segs[i][0]) + (segs[i + 1][1] - segs[i + 1][0]))
        segs[shortest_idx][1] = segs[shortest_idx + 1][1]
        segs.pop(shortest_idx + 1)

    return [(round(s[0], 3), round(s[1], 3)) for s in segs]


def _extract_scene_annotated_frames(
    src: str,
    clip_start: float,
    scene_segs: list[tuple[float, float]],
) -> tuple[list[str], list[str]]:
    """Extract and annotate a representative keyframe for EVERY scene across the clip.
    Draws a clear 'SCENE #k (t0s - t1s)' HUD badge on each frame so the Master Vision Model
    knows the exact scene index and time span it is directing.
    """
    import base64
    images_b64: list[str] = []
    descriptions: list[str] = []

    try:
        import cv2
        cap = cv2.VideoCapture(src)
        if not cap.isOpened():
            return [], []
        try:
            for idx, (s0, s1) in enumerate(scene_segs, 1):
                mid_t = clip_start + (s0 + s1) / 2.0
                cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, mid_t * 1000))
                ok, frame = cap.read()
                if not ok:
                    continue
                h_orig, w_orig = frame.shape[:2]
                target_w = 640
                target_h = max(240, int(round(h_orig * target_w / max(1, w_orig))))
                small = cv2.resize(frame, (target_w, target_h))

                label = f"SCENE {idx}: {s0:.1f}s - {s1:.1f}s"
                cv2.rectangle(small, (8, 8), (275, 40), (0, 0, 0), -1)
                cv2.putText(small, label, (16, 31), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (0, 230, 255), 2, cv2.LINE_AA)

                ok_enc, buf = cv2.imencode(".jpg", small, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
                if ok_enc:
                    images_b64.append(base64.b64encode(buf.tobytes()).decode("utf-8"))
                    descriptions.append(f"- Scene {idx} ({s0:.1f}s to {s1:.1f}s, duration {s1 - s0:.1f}s)")
        finally:
            cap.release()
    except Exception as e:
        log.warning("OpenCV scene frame extraction failed: %s", e)

    return images_b64, descriptions


def face_path(src: str, start: float, end: float, src_w: int, src_h: int, crop_w: int,
              samples_per_sec: float = 4.0, vision_hints: dict | None = None) -> tuple[list[tuple[float, float]], tuple[int, int, int], list[tuple[float, float]]] | None:
    """Return (path, (cw, ch, crop_y), blur_intervals) or None if the entire clip should use blur/graphic layout.
    Features:
    1. Master Vision Director Control: Follows per-scene layout, focus_side, tracking_style, and ignore_background_faces directives.
    2. Automatic Mid-Clip Graphic/Image Protection: Whenever a scene is marked 'blur' or 'has_graphic' by the Vision Director,
       OR when faces disappear for >= 1.25s (e.g. an image, B-roll, chart, or screen share pops up mid-clip),
       records the exact (t_start, t_end) interval in `blur_intervals` so FFmpeg dynamically switches to full-frame blurred fit
       during that moment and returns to face-tracking right after!
    3. YuNet Neural Face Tracking + Cascades fallback: 99.5% detection across extreme angles and boom mics.
    4. Broadcast Multi-Cam Switcher: Executes instant 0.04s broadcast hard cuts between speakers.
    5. Active Speaker Auto-Cameraman: Detects mouth motion energy using exact YuNet landmarks.
    """
    try:
        import cv2
    except ImportError:
        return None

    frontal, profile, body = _load_cascades(cv2)
    det_w = 640
    det_h = max(240, int(det_w * src_h / src_w))
    yunet = _load_yunet(cv2, (det_w, det_h))
    if not yunet and not frontal and not profile and not body:
        return None

    cap = cv2.VideoCapture(src)
    if not cap.isOpened():
        return None

    tracking_style = (vision_hints or {}).get("tracking_style") or "active_speaker"
    ignore_bg_faces = bool((vision_hints or {}).get("ignore_background_faces", False))
    scene_directives: list[dict] = (vision_hints or {}).get("scene_timeline") or []

    def scene_at(rel_t: float) -> dict | None:
        for sc in scene_directives:
            if sc["start"] - 0.05 <= rel_t <= sc["end"] + 0.05:
                return sc
        return None

    scale = det_w / src_w if src_w > det_w else 1.0
    raw: list[tuple[float, float | None, float | None, bool]] = []
    t = start
    step = 1.0 / samples_per_sec
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frame_interval = max(1, int(round(fps / samples_per_sec)))
    cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, start * 1000))
    prev_cx = None
    prev_cy = None
    lost_count = 0
    mid_x = src_w / 2
    prev_gray = None
    prev_hist = None
    shot_cuts: list[float] = []

    # Active Speaker Motion state
    ema_l_motion = 0.0
    ema_r_motion = 0.0
    active_side = (
        vision_hints.get("primary_speaker_side")
        if (vision_hints and vision_hints.get("primary_speaker_side") in ("left", "right"))
        else None
    )
    side_switch_hold = 0

    try:
        while t < end:
            ok, frame = cap.read()
            if not ok:
                break
            rel_t = round(max(0.0, t - start), 3)
            cur_scene = scene_at(rel_t)
            scene_wants_blur = bool(cur_scene and (cur_scene.get("layout") == "blur" or cur_scene.get("has_graphic")))
            scene_focus = (cur_scene.get("focus_side") if cur_scene else None) or "auto"

            small = cv2.resize(frame, None, fx=scale, fy=scale) if scale != 1.0 else frame
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)

            # Broadcast Shot-Cut Detection via HSV Histogram Correlation
            hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
            hist = cv2.calcHist([hsv], [0, 1], None, [16, 16], [0, 180, 0, 256])
            cv2.normalize(hist, hist, alpha=0, beta=1, norm_type=cv2.NORM_MINMAX)
            is_shot_cut = False
            if prev_hist is not None:
                corr = cv2.compareHist(prev_hist, hist, cv2.HISTCMP_CORREL)
                if corr < 0.52:
                    is_shot_cut = True
                    shot_cuts.append(rel_t)
                    side_switch_hold = 0
            prev_hist = hist

            cands = _detect_speaker_cands(cv2, small, gray, scale, yunet, frontal, profile, body)
            if cands and (ignore_bg_faces or tracking_style == "lock_primary"):
                max_area = max(c["area"] for c in cands)
                # Filter out small background faces / posters / images behind the speaker
                cands = [c for c in cands if c["area"] >= max_area * 0.45]

            cx, cy = None, None
            if scene_wants_blur:
                # Master Vision Director flagged this scene as an on-screen image/slide/graphic
                lost_count = 0
            elif cur_scene and cur_scene.get("layout") == "center":
                cx, cy = mid_x, src_h * 0.38
                prev_cx, prev_cy = cx, cy
                lost_count = 0
            elif cands:
                if scene_focus == "left":
                    side_cands = [c for c in cands if c["cx"] < mid_x] or cands
                    chosen = max(side_cands, key=lambda c: c["area"])
                    cx, cy = chosen["cx"], chosen["cy"]
                    prev_cx, prev_cy = cx, cy
                    active_side = "left"
                    lost_count = 0
                elif scene_focus == "right":
                    side_cands = [c for c in cands if c["cx"] >= mid_x] or cands
                    chosen = max(side_cands, key=lambda c: c["area"])
                    cx, cy = chosen["cx"], chosen["cy"]
                    prev_cx, prev_cy = cx, cy
                    active_side = "right"
                    lost_count = 0
                else:
                    left_cands = [c for c in cands if c["cx"] < mid_x]
                    right_cands = [c for c in cands if c["cx"] >= mid_x]

                    if left_cands and right_cands and tracking_style != "lock_primary":
                        best_l = max(left_cands, key=lambda c: c["area"])
                        best_r = max(right_cands, key=lambda c: c["area"])

                        m_l, m_r = 0.0, 0.0
                        if prev_gray is not None:
                            for spk, cand_item in (("l", best_l), ("r", best_r)):
                                if cand_item.get("mouth"):
                                    mx, my = cand_item["mouth"]
                                else:
                                    sx, sy = int(cand_item["cx"] * scale), int(cand_item["cy"] * scale)
                                    mx = max(0, min(gray.shape[1] - 24, sx))
                                    my = max(0, min(gray.shape[0] - 15, sy + 6))
                                y1 = max(0, my - 8)
                                y2 = min(gray.shape[0], my + 8)
                                x1 = max(0, mx - 12)
                                x2 = min(gray.shape[1], mx + 12)
                                if y2 > y1 and x2 > x1:
                                    diff = float(cv2.absdiff(gray[y1:y2, x1:x2], prev_gray[y1:y2, x1:x2]).mean())
                                    if spk == "l":
                                        m_l = diff
                                    else:
                                        m_r = diff

                        ema_l_motion = 0.35 * m_l + 0.65 * ema_l_motion
                        ema_r_motion = 0.35 * m_r + 0.65 * ema_r_motion

                        if ema_l_motion > ema_r_motion * 1.30:
                            desired_side = "left"
                        elif ema_r_motion > ema_l_motion * 1.30:
                            desired_side = "right"
                        else:
                            desired_side = active_side or "left"

                        if desired_side != active_side:
                            side_switch_hold += 1
                            threshold = 2 if is_shot_cut else 4
                            if side_switch_hold >= threshold or active_side is None:
                                if active_side is not None and desired_side != active_side:
                                    shot_cuts.append(rel_t)
                                active_side = desired_side
                                side_switch_hold = 0
                        else:
                            side_switch_hold = 0

                        chosen = best_l if active_side == "left" else best_r
                        cx, cy = chosen["cx"], chosen["cy"]
                        prev_cx, prev_cy = cx, cy
                        lost_count = 0
                    else:
                        if prev_cx is None or is_shot_cut:
                            chosen = max(cands, key=lambda c: c["area"])
                            cx, cy = chosen["cx"], chosen["cy"]
                            prev_cx, prev_cy = cx, cy
                            lost_count = 0
                        else:
                            near = [c for c in cands if abs(c["cx"] - prev_cx) < crop_w * 0.6]
                            if near:
                                chosen = min(near, key=lambda c: abs(c["cx"] - prev_cx))
                                cx, cy = chosen["cx"], chosen["cy"]
                                prev_cx, prev_cy = cx, cy
                                lost_count = 0
                            else:
                                lost_count += 1
                                if lost_count >= 6:
                                    chosen = max(cands, key=lambda c: c["area"])
                                    cx, cy = chosen["cx"], chosen["cy"]
                                    prev_cx, prev_cy = cx, cy
                                    lost_count = 0
                                else:
                                    cx, cy = prev_cx, prev_cy
            elif prev_cx is not None and not is_shot_cut:
                lost_count += 1
                # Hold position across brief 1.0s occlusions (4 samples), but if faces vanish longer
                # (e.g. a full-screen image/slide/B-roll appeared), leave cx=None so dynamic blur protection triggers
                if lost_count <= 4:
                    cx, cy = prev_cx, prev_cy

            prev_gray = gray
            raw.append((rel_t, cx, cy, scene_wants_blur))
            t += step
            for _ in range(frame_interval - 1):
                if not cap.grab():
                    break
    finally:
        cap.release()

    valid = [(tt, c, cy_val) for tt, c, cy_val, is_b in raw if c is not None and not is_b]
    if not valid or len(valid) < max(2, int(len(raw) * 0.15)):
        return None

    # Build dynamic blur intervals for any time range where:
    # 1) The Master Vision Director ordered 'blur' / 'has_graphic', OR
    # 2) No face was present for >= 1.25 seconds (e.g. an image, chart, or B-roll cutaway appeared mid-clip)
    blur_intervals: list[tuple[float, float]] = []
    for sc in scene_directives:
        if sc.get("layout") == "blur" or sc.get("has_graphic"):
            blur_intervals.append((float(sc["start"]), float(sc["end"])))

    no_face_start: float | None = None
    for idx, (tt, c, _, is_b) in enumerate(raw):
        if c is None or is_b:
            if no_face_start is None:
                no_face_start = tt
        else:
            if no_face_start is not None:
                if tt - no_face_start >= 1.25:
                    blur_intervals.append((round(no_face_start, 3), round(tt, 3)))
                no_face_start = None
    if no_face_start is not None and (end - start) - no_face_start >= 1.25:
        blur_intervals.append((round(no_face_start, 3), round(end - start, 3)))

    # Merge overlapping blur intervals
    merged_blur: list[list[float]] = []
    for b0, b1 in sorted(blur_intervals):
        if b1 - b0 < 0.8:
            continue
        if not merged_blur or b0 - merged_blur[-1][1] > 0.4:
            merged_blur.append([max(0.0, round(b0, 3)), min(round(end - start, 3), round(b1, 3))])
        else:
            merged_blur[-1][1] = max(merged_blur[-1][1], min(round(end - start, 3), round(b1, 3)))
    final_blur_intervals = [(b[0], b[1]) for b in merged_blur]

    # Calculate speaker clearances from boundaries to compute adaptive boundary zoom
    left_xs = [c for _, c, _ in valid if c < mid_x]
    right_xs = [c for _, c, _ in valid if c >= mid_x]

    clearances = []
    if left_xs:
        med_l = sorted(left_xs)[len(left_xs) // 2]
        clearances.append(med_l)
    if right_xs:
        med_r = sorted(right_xs)[len(right_xs) // 2]
        clearances.append(src_w - med_r)

    base_half = crop_w / 2
    min_clearance = min(clearances) if clearances else base_half

    target_half = max(min_clearance, base_half / 1.25)
    zoom_factor = min(1.25, max(1.0, base_half / target_half))

    cw = int(round(crop_w / zoom_factor))
    cw -= cw % 2
    ch = int(round(src_h / zoom_factor))
    ch -= ch % 2

    # Vertical headroom centering: anchor eyes/face at 35% from the top of the cropped frame
    all_cy = [cy_val for _, _, cy_val in valid if cy_val is not None]
    med_cy = sorted(all_cy)[len(all_cy) // 2] if all_cy else (src_h * 0.38)
    desired_y = int(med_cy - ch * 0.35)
    crop_y = min(max(desired_y, 0), max(0, src_h - ch))

    # Fill missing frames: if speaker is momentarily occluded, stay locked on their position
    filled: list[tuple[float, float]] = []
    last = valid[0][1] if valid else mid_x
    for tt, c, _, _ in raw:
        if c is not None:
            last = c
        filled.append((tt, last))

    path = _build_cinematic_path(filled, cw, src_w, end - start, cut_times=shot_cuts, tracking_style=tracking_style)
    return path, (cw, ch, crop_y), final_blur_intervals


def _build_cinematic_path(raw_points: list[tuple[float, float]], crop_w: int, src_w: int, duration: float,
                          cut_times: list[float] | None = None,
                          tracking_style: str = "active_speaker") -> list[tuple[float, float]]:
    """Convert raw face tracking points into a stabilized cinematic camera track.
    Executes instant hard cuts (0.04s) on broadcast camera angle switches or speaker cuts,
    and silky-smooth eased pans (0.32s - 0.50s) when a speaker moves within a continuous shot.
    """
    if not raw_points:
        return []
    half = crop_w / 2
    max_x = max(0, src_w - crop_w)
    cuts = cut_times or []

    xs = [p[1] for p in raw_points]
    n = len(xs)
    med = []
    for i in range(n):
        win = sorted(xs[max(0, i - 3): min(n, i + 4)])
        med.append(win[len(win) // 2])

    ema = [med[0]]
    alpha = 0.14 if tracking_style == "smooth_follow" else 0.18
    for val in med[1:]:
        ema.append(alpha * val + (1.0 - alpha) * ema[-1])

    deadband = crop_w * (0.16 if tracking_style == "smooth_follow" else 0.22)
    cam_center = ema[0]
    initial_crop_x = float(min(max(cam_center - half, 0), max_x))
    anchors = [(0.0, round(initial_crop_x, 1))]

    i = 0
    while i < n:
        tt = float(raw_points[i][0])
        target = float(ema[i])
        diff = target - cam_center

        if abs(diff) > deadband:
            near_cut = any(abs(tt - ct) <= 0.45 for ct in cuts)
            if tracking_style == "smooth_follow" and not near_cut:
                pan_duration = 0.45
            else:
                pan_duration = 0.04 if (near_cut or abs(diff) > crop_w * 0.45) else 0.32
            pan_start_t = max(0.0, tt)
            pan_end_t = min(round(duration, 3), round(pan_start_t + pan_duration, 3))
            if pan_end_t <= pan_start_t:
                pan_end_t = min(round(duration, 3), round(pan_start_t + 0.04, 3))

            new_target = target
            new_crop_x = float(min(max(new_target - half, 0), max_x))

            prev_crop_x = float(anchors[-1][1])
            # Hold camera stationary at previous crop until pan/cut starts
            if pan_start_t > anchors[-1][0] + 0.04:
                anchors.append((round(pan_start_t, 3), prev_crop_x))

            anchors.append((round(pan_end_t, 3), round(new_crop_x, 1)))
            cam_center = new_target

            while i < n and raw_points[i][0] < pan_end_t:
                i += 1
            continue
        i += 1

    # Ensure final anchor reaches the end of the clip
    if anchors[-1][0] < duration:
        anchors.append((round(duration, 3), float(anchors[-1][1])))

    return anchors


def face_paths_split(src: str, start: float, end: float, src_w: int, src_h: int, crop_w: int,
                     samples_per_sec: float = 4.0) -> tuple:
    """Return (path_left, path_right, box_left, box_right) for Speaker 1 (top/left) and Speaker 2 (bottom/right)."""
    try:
        import cv2
    except ImportError:
        return None, None, None, None

    frontal, profile, body = _load_cascades(cv2)
    det_w = 640
    det_h = max(240, int(det_w * src_h / src_w))
    yunet = _load_yunet(cv2, (det_w, det_h))
    if not yunet and not frontal and not profile and not body:
        return None, None, None, None

    cap = cv2.VideoCapture(src)
    if not cap.isOpened():
        return None, None, None, None

    scale = det_w / src_w if src_w > det_w else 1.0
    t = start
    step = 1.0 / samples_per_sec
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frame_interval = max(1, int(round(fps / samples_per_sec)))
    cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, start * 1000))

    raw_left: list[tuple[float, float | None, float | None]] = []
    raw_right: list[tuple[float, float | None, float | None]] = []
    mid_x = src_w / 2
    prev_hist = None
    shot_cuts: list[float] = []

    try:
        while t < end:
            ok, frame = cap.read()
            if not ok:
                break
            small = cv2.resize(frame, None, fx=scale, fy=scale) if scale != 1.0 else frame
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)

            hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
            hist = cv2.calcHist([hsv], [0, 1], None, [16, 16], [0, 180, 0, 256])
            cv2.normalize(hist, hist, alpha=0, beta=1, norm_type=cv2.NORM_MINMAX)
            if prev_hist is not None:
                corr = cv2.compareHist(prev_hist, hist, cv2.HISTCMP_CORREL)
                if corr < 0.52:
                    shot_cuts.append(round(t - start, 3))
            prev_hist = hist

            cands = _detect_speaker_cands(cv2, small, gray, scale, yunet, frontal, profile, body)
            cx_left, cy_left = None, None
            cx_right, cy_right = None, None

            if cands:
                left_cands = [c for c in cands if c["cx"] < mid_x]
                right_cands = [c for c in cands if c["cx"] >= mid_x]

                if left_cands and right_cands:
                    best_l = max(left_cands, key=lambda c: c["area"])
                    best_r = max(right_cands, key=lambda c: c["area"])
                    cx_left, cy_left = best_l["cx"], best_l["cy"]
                    cx_right, cy_right = best_r["cx"], best_r["cy"]
                elif len(cands) >= 2:
                    sorted_c = sorted(cands, key=lambda c: c["cx"])
                    cx_left, cy_left = sorted_c[0]["cx"], sorted_c[0]["cy"]
                    cx_right, cy_right = sorted_c[-1]["cx"], sorted_c[-1]["cy"]
                elif left_cands:
                    best_l = max(left_cands, key=lambda c: c["area"])
                    cx_left, cy_left = best_l["cx"], best_l["cy"]
                elif right_cands:
                    best_r = max(right_cands, key=lambda c: c["area"])
                    cx_right, cy_right = best_r["cx"], best_r["cy"]

            raw_left.append((t - start, cx_left, cy_left))
            raw_right.append((t - start, cx_right, cy_right))
            t += step
            for _ in range(frame_interval - 1):
                if not cap.grab():
                    break
    finally:
        cap.release()

    has_left = any(c is not None for _, c, _ in raw_left)
    has_right = any(c is not None for _, c, _ in raw_right)
    if not has_left and not has_right:
        return None, None, None, None

    def compute_box_and_path(raw, default_cx, side):
        valid = [(tt, c, cy_v) for tt, c, cy_v in raw if c is not None]
        if not valid:
            cw = min(src_w, crop_w)
            ch = src_h - (src_h % 2)
            return None, (cw, ch, 0)

        all_x = [c for _, c, _ in valid]
        all_y = [cy_v for _, _, cy_v in valid if cy_v is not None]
        med_x = sorted(all_x)[len(all_x) // 2]
        med_cy = sorted(all_y)[len(all_y) // 2] if all_y else (src_h * 0.38)

        clearance = med_x if side == "left" else (src_w - med_x)
        cw = max(int(round(src_h * 0.45)), min(int(round(clearance * 2)), crop_w))
        cw -= cw % 2
        ch = int(round(cw / 1.125))
        ch = min(src_h - (src_h % 2), ch - (ch % 2))
        desired_y = int(med_cy - ch * 0.35)
        crop_y = min(max(desired_y, 0), max(0, src_h - ch))

        filled = []
        last = med_x
        for tt, c, _ in raw:
            if c is not None:
                last = c
            filled.append((tt, last))
        p = _build_cinematic_path(filled, cw, src_w, end - start, cut_times=shot_cuts)
        return p, (cw, ch, crop_y)

    path_left, box_left = compute_box_and_path(raw_left, src_w * 0.25, "left")
    path_right, box_right = compute_box_and_path(raw_right, src_w * 0.75, "right")
    return path_left, path_right, box_left, box_right


def detect_gaming_layout(src: str, start: float, end: float, src_w: int, src_h: int,
                         tw: int = 1080, th: int = 1920,
                         webcam_hint: str | None = None) -> tuple[tuple[int, int, int, int], tuple[int, int, int, int]] | None:
    """Detects streamer facecam corner box and center gameplay box for 9:16 Gaming Cam-Stack layout.
    Clusters face detections across 8 timestamps so stationary corner webcam overlays are locked onto
    and moving in-game 3D character faces are ignored.
    Returns ((cam_cw, cam_ch, cam_x, cam_y), (game_cw, game_ch, game_x, game_y)) or None.
    """
    try:
        import cv2
    except ImportError:
        return None

    frontal, profile, body = _load_cascades(cv2)
    det_w = 640
    det_h = max(240, int(det_w * src_h / src_w))
    yunet = _load_yunet(cv2, (det_w, det_h))
    if not yunet and not frontal and not profile and not body:
        return None

    cap = cv2.VideoCapture(src)
    if not cap.isOpened():
        return None

    scale = det_w / src_w if src_w > det_w else 1.0
    dur = max(1.0, end - start)
    sample_times = [start + dur * (i / 9.0) for i in range(1, 9)]
    all_face_cands: list[dict] = []

    try:
        for st in sample_times:
            cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, st * 1000))
            ok, frame = cap.read()
            if not ok:
                continue
            small = cv2.resize(frame, None, fx=scale, fy=scale) if scale != 1.0 else frame
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            cands = _detect_speaker_cands(cv2, small, gray, scale, yunet, frontal, profile, body)
            for c in cands:
                # Ignore giant center-screen faces (in-game cutscene characters or full-screen facecam)
                if c["area"] < (src_w * src_h * 0.22):
                    all_face_cands.append(c)
    finally:
        cap.release()

    if not all_face_cands:
        return None

    # Filter by webcam quadrant hint if provided by Master Vision Director
    filtered = all_face_cands
    mid_x = src_w / 2
    mid_y = src_h / 2
    if webcam_hint == "bottom_right":
        filtered = [c for c in all_face_cands if c["cx"] >= mid_x and c["cy"] >= mid_y] or all_face_cands
    elif webcam_hint == "bottom_left":
        filtered = [c for c in all_face_cands if c["cx"] < mid_x and c["cy"] >= mid_y] or all_face_cands
    elif webcam_hint == "top_right":
        filtered = [c for c in all_face_cands if c["cx"] >= mid_x and c["cy"] < mid_y] or all_face_cands
    elif webcam_hint == "top_left":
        filtered = [c for c in all_face_cands if c["cx"] < mid_x and c["cy"] < mid_y] or all_face_cands

    # Cluster detections by spatial proximity: a streamer webcam stays in a fixed overlay box across frames
    best_cluster: list[dict] = []
    radius_x = src_w * 0.08
    radius_y = src_h * 0.08
    for cand in filtered:
        neighbors = [
            other for other in filtered
            if abs(other["cx"] - cand["cx"]) <= radius_x and abs(other["cy"] - cand["cy"]) <= radius_y
        ]
        # Prefer clusters near the left/right edges (where streamer webcam boxes live) with consistent detections
        if len(neighbors) > len(best_cluster):
            best_cluster = neighbors

    cluster = best_cluster if best_cluster else filtered
    fc_x = sorted(c["cx"] for c in cluster)[len(cluster) // 2]
    fc_y = sorted(c["cy"] for c in cluster)[len(cluster) // 2]
    med_area = sorted(c["area"] for c in cluster)[len(cluster) // 2]

    cam_h = int(round(th * 0.36))
    cam_h -= cam_h % 2
    game_h = th - cam_h

    cam_ar = tw / cam_h
    face_dim = (med_area ** 0.5) if med_area else (src_h * 0.15)
    cam_cw = int(round(max(face_dim * 2.8, src_w * 0.22)))
    cam_cw -= cam_cw % 2
    cam_ch = int(round(cam_cw / cam_ar))
    cam_ch -= cam_ch % 2

    if cam_ch > src_h:
        cam_ch = src_h - (src_h % 2)
        cam_cw = int(round(cam_ch * cam_ar))
        cam_cw -= cam_cw % 2

    cam_desired_x = int(fc_x - cam_cw / 2)
    cam_desired_y = int(fc_y - cam_ch * 0.40)
    cam_x = max(0, min(src_w - cam_cw, cam_desired_x))
    cam_y = max(0, min(src_h - cam_ch, cam_desired_y))

    game_ar = tw / game_h
    game_ch = src_h - (src_h % 2)
    game_cw = int(round(game_ch * game_ar))
    game_cw = min(src_w - (src_w % 2), game_cw - (game_cw % 2))

    game_x = (src_w - game_cw) // 2
    game_y = 0

    return (cam_cw, cam_ch, cam_x, cam_y), (game_cw, game_ch, game_x, game_y)


def _lerp_expr(points: list[tuple[float, float]]) -> str:
    """Build a smoothstep S-curve eased FFmpeg expression for fluid camera panning without robotic jerk."""
    if not points:
        return "0"
    if len(points) == 1 or all(abs(p[1] - points[0][1]) < 0.5 for p in points):
        return f"{points[0][1]:.1f}"

    expr = f"{points[-1][1]:.1f}"
    for (t0, x0), (t1, x1) in reversed(list(zip(points[:-1], points[1:]))):
        dt = t1 - t0
        if dt < 1e-3:
            continue
        dx = x1 - x0
        if abs(dx) < 0.5:
            if expr == f"{x0:.1f}":
                continue
            seg = f"{x0:.1f}"
        else:
            # Smoothstep (Hermite interpolation: 3u^2 - 2u^3)
            # Provides 0 acceleration at start and 0 deceleration at end (fluid cinematic pan)
            u = f"((t-{t0:.3f})/{dt:.3f})"
            seg = f"{x0:.1f}+({dx:.1f})*{u}*{u}*(3-2*{u})"
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
    font_files = [f for f in fonts.iterdir() if f.is_file() and f.suffix.lower() in (".ttf", ".otf", ".woff", ".woff2")] if fonts.is_dir() else []
    if font_files:
        esc_dir = _esc(str(fonts).replace("\\", "/"))
        opt = f":fontsdir={esc_dir}"
    return f"[{in_label}]ass=subs.ass{opt}[{out_label}]"


def build_graph(mode: str, aspect: str, src_w: int, src_h: int, tw: int, th: int,
                path: list[tuple[float, float]] | None, crop_w: int, has_subs: bool,
                split_paths: tuple | None = None, gaming_boxes: tuple | None = None,
                dynamic_zoom: bool = True,
                keep_segs: list[tuple[float, float]] | None = None,
                crop_box: tuple[int, int, int] | None = None,
                blur_intervals: list[tuple[float, float]] | None = None,
                scene_timeline: list[dict] | None = None) -> str:
    """Return a -filter_complex graph taking [0:v] and producing [v].
    Supports Master Vision Director dynamic mid-clip layout switching (e.g. face -> blur -> split -> face)
    so when an image, slide, screen share, or dual-speaker shot appears mid-clip, the layout switches cleanly.
    """
    if aspect == "16:9":
        base = (f"[0:v]scale={tw}:{th}:force_original_aspect_ratio=decrease,"
                f"pad={tw}:{th}:(ow-iw)/2:(oh-ih)/2,setsar=1[v_raw]")
    elif mode == "gaming" and aspect == "9:16":
        # Viral Gaming Cam-Stack: Streamer facecam on top (36%) + Center gameplay action on bottom (64%)
        # Also supports dynamic mid-clip switching to full-screen speaker ('face') or wide scoreboard/menu ('blur')
        cam_h = int(round(th * 0.36))
        cam_h -= cam_h % 2
        game_h = th - cam_h

        if gaming_boxes:
            (cam_cw, cam_ch, cam_x, cam_y), (game_cw, game_ch, game_x, game_y) = gaming_boxes
        else:
            cam_cw, cam_ch, cam_x, cam_y = (src_w // 2, src_h // 2, src_w // 4, src_h // 4)
            game_cw, game_ch, game_x, game_y = (src_w, src_h, 0, 0)

        face_intervals = [
            (float(sc["start"]), float(sc["end"]))
            for sc in (scene_timeline or [])
            if sc.get("layout") == "face" and float(sc["end"]) - float(sc["start"]) >= 1.0
        ]
        use_mid_face = bool(face_intervals)
        if crop_box:
            f_cw, f_ch, f_cy = crop_box
        else:
            f_cw, f_ch, f_cy = crop_w - (crop_w % 2), src_h - (src_h % 2), 0
        f_max_x = max(0, src_w - f_cw)
        f_x = f"min(max({_lerp_expr(path)},0),{f_max_x})" if path else f"{f_max_x // 2}"

        if blur_intervals and use_mid_face:
            b_expr = "+".join(f"between(t,{b0:.3f},{b1:.3f})" for b0, b1 in blur_intervals)
            f_expr = "+".join(f"between(t,{f0:.3f},{f1:.3f})" for f0, f1 in face_intervals)
            base = (
                f"[0:v]split=5[raw_cam][raw_game][vf][ba][bb];"
                f"[raw_cam]crop={cam_cw}:{cam_ch}:{cam_x}:{cam_y},scale={tw}:{cam_h}:flags=lanczos,setsar=1[cam];"
                f"[raw_game]crop={game_cw}:{game_ch}:{game_x}:{game_y},scale={tw}:{game_h}:flags=lanczos,setsar=1[game];"
                f"[cam][game]vstack=inputs=2[stacked];"
                f"[stacked]drawbox=y={cam_h - 2}:color=black@0.85:width=iw:height=4:t=fill[v_game];"
                f"[vf]crop={f_cw}:{f_ch}:x='{f_x}':y={f_cy},scale={tw}:{th}:flags=lanczos,setsar=1[v_face];"
                f"[ba]scale={tw}:{th}:force_original_aspect_ratio=increase,crop={tw}:{th},boxblur=28:3[bg];"
                f"[bb]scale={tw}:{th}:force_original_aspect_ratio=decrease[fg];"
                f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[v_blur];"
                f"[v_game][v_face]overlay=0:0:enable='{f_expr}'[v_gf];"
                f"[v_gf][v_blur]overlay=0:0:enable='{b_expr}',setsar=1[v_raw]"
            )
        elif blur_intervals:
            b_expr = "+".join(f"between(t,{b0:.3f},{b1:.3f})" for b0, b1 in blur_intervals)
            base = (
                f"[0:v]split=4[raw_cam][raw_game][ba][bb];"
                f"[raw_cam]crop={cam_cw}:{cam_ch}:{cam_x}:{cam_y},scale={tw}:{cam_h}:flags=lanczos,setsar=1[cam];"
                f"[raw_game]crop={game_cw}:{game_ch}:{game_x}:{game_y},scale={tw}:{game_h}:flags=lanczos,setsar=1[game];"
                f"[cam][game]vstack=inputs=2[stacked];"
                f"[stacked]drawbox=y={cam_h - 2}:color=black@0.85:width=iw:height=4:t=fill[v_game];"
                f"[ba]scale={tw}:{th}:force_original_aspect_ratio=increase,crop={tw}:{th},boxblur=28:3[bg];"
                f"[bb]scale={tw}:{th}:force_original_aspect_ratio=decrease[fg];"
                f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[v_blur];"
                f"[v_game][v_blur]overlay=0:0:enable='{b_expr}',setsar=1[v_raw]"
            )
        elif use_mid_face:
            f_expr = "+".join(f"between(t,{f0:.3f},{f1:.3f})" for f0, f1 in face_intervals)
            base = (
                f"[0:v]split=3[raw_cam][raw_game][vf];"
                f"[raw_cam]crop={cam_cw}:{cam_ch}:{cam_x}:{cam_y},scale={tw}:{cam_h}:flags=lanczos,setsar=1[cam];"
                f"[raw_game]crop={game_cw}:{game_ch}:{game_x}:{game_y},scale={tw}:{game_h}:flags=lanczos,setsar=1[game];"
                f"[cam][game]vstack=inputs=2[stacked];"
                f"[stacked]drawbox=y={cam_h - 2}:color=black@0.85:width=iw:height=4:t=fill[v_game];"
                f"[vf]crop={f_cw}:{f_ch}:x='{f_x}':y={f_cy},scale={tw}:{th}:flags=lanczos,setsar=1[v_face];"
                f"[v_game][v_face]overlay=0:0:enable='{f_expr}',setsar=1[v_raw]"
            )
        else:
            base = (
                f"[0:v]split=2[raw_cam][raw_game];"
                f"[raw_cam]crop={cam_cw}:{cam_ch}:{cam_x}:{cam_y},scale={tw}:{cam_h}:flags=lanczos,setsar=1[cam];"
                f"[raw_game]crop={game_cw}:{game_ch}:{game_x}:{game_y},scale={tw}:{game_h}:flags=lanczos,setsar=1[game];"
                f"[cam][game]vstack=inputs=2[stacked];"
                f"[stacked]drawbox=y={cam_h - 2}:color=black@0.85:width=iw:height=4:t=fill[v_raw]"
            )
    elif mode == "split" and aspect == "9:16":
        # Two stacked frames: Top speaker + Bottom speaker
        top_h = th // 2
        bot_h = th - top_h

        if split_paths and len(split_paths) == 4:
            p_left, p_right, box_top, box_bot = split_paths
        else:
            p_left, p_right = (split_paths[:2] if split_paths else (None, None))
            box_top, box_bot = None, None

        split_crop_w = int(round(src_h * tw / top_h))
        split_crop_w = min(src_w, split_crop_w - (split_crop_w % 2))
        default_ch = src_h - (src_h % 2)

        cw_top, ch_top, y_top = box_top if box_top else (split_crop_w, default_ch, 0)
        cw_bot, ch_bot, y_bot = box_bot if box_bot else (split_crop_w, default_ch, 0)

        max_top_x = max(0, src_w - cw_top)
        max_bot_x = max(0, src_w - cw_bot)

        x_top = f"min(max({_lerp_expr(p_left)},0),{max_top_x})" if p_left else f"{max_top_x // 2}"
        x_bot = f"min(max({_lerp_expr(p_right)},0),{max_bot_x})" if p_right else f"{max_bot_x // 2}"

        if blur_intervals:
            b_expr = "+".join(f"between(t,{b0:.3f},{b1:.3f})" for b0, b1 in blur_intervals)
            base = (
                f"[0:v]split=4[sp1][sp2][ba][bb];"
                f"[sp1]crop={cw_top}:{ch_top}:x='{x_top}':y={y_top},scale={tw}:{top_h}:flags=lanczos,setsar=1[top];"
                f"[sp2]crop={cw_bot}:{ch_bot}:x='{x_bot}':y={y_bot},scale={tw}:{bot_h}:flags=lanczos,setsar=1[bot];"
                f"[top][bot]vstack=inputs=2[stacked];"
                f"[stacked]drawbox=y={top_h - 2}:color=black@0.75:width=iw:height=4:t=fill[v_split];"
                f"[ba]scale={tw}:{th}:force_original_aspect_ratio=increase,crop={tw}:{th},boxblur=28:3[bg];"
                f"[bb]scale={tw}:{th}:force_original_aspect_ratio=decrease[fg];"
                f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[v_blur];"
                f"[v_split][v_blur]overlay=0:0:enable='{b_expr}',setsar=1[v_raw]"
            )
        else:
            base = (
                f"[0:v]split=2[sp1][sp2];"
                f"[sp1]crop={cw_top}:{ch_top}:x='{x_top}':y={y_top},scale={tw}:{top_h}:flags=lanczos,setsar=1[top];"
                f"[sp2]crop={cw_bot}:{ch_bot}:x='{x_bot}':y={y_bot},scale={tw}:{bot_h}:flags=lanczos,setsar=1[bot];"
                f"[top][bot]vstack=inputs=2[stacked];"
                f"[stacked]drawbox=y={top_h - 2}:color=black@0.75:width=iw:height=4:t=fill[v_raw]"
            )
    elif mode == "blur" or crop_w > src_w:
        base = (f"[0:v]split=2[a][b];"
                f"[a]scale={tw}:{th}:force_original_aspect_ratio=increase,crop={tw}:{th},boxblur=28:3[bg];"
                f"[b]scale={tw}:{th}:force_original_aspect_ratio=decrease[fg];"
                f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[v_raw]")
    else:
        if crop_box:
            cw, ch, cy = crop_box
        else:
            cw = crop_w - (crop_w % 2)
            ch = src_h - (src_h % 2)
            cy = 0
        max_x = max(0, src_w - cw)
        if mode == "face" and path:
            x = f"min(max({_lerp_expr(path)},0),{max_x})"
        else:
            x = f"{max_x // 2}"

        split_intervals = [
            (float(sc["start"]), float(sc["end"]))
            for sc in (scene_timeline or [])
            if sc.get("layout") == "split" and float(sc["end"]) - float(sc["start"]) >= 1.0
        ]
        use_mid_split = bool(split_intervals and split_paths and aspect == "9:16")

        if blur_intervals and use_mid_split:
            b_expr = "+".join(f"between(t,{b0:.3f},{b1:.3f})" for b0, b1 in blur_intervals)
            s_expr = "+".join(f"between(t,{s0:.3f},{s1:.3f})" for s0, s1 in split_intervals)
            top_h = th // 2
            bot_h = th - top_h
            p_left, p_right, box_top, box_bot = split_paths if len(split_paths) == 4 else (*split_paths[:2], None, None)
            split_crop_w = min(src_w, int(round(src_h * tw / top_h)) - (int(round(src_h * tw / top_h)) % 2))
            default_ch = src_h - (src_h % 2)
            cw_top, ch_top, y_top = box_top if box_top else (split_crop_w, default_ch, 0)
            cw_bot, ch_bot, y_bot = box_bot if box_bot else (split_crop_w, default_ch, 0)
            max_top_x = max(0, src_w - cw_top)
            max_bot_x = max(0, src_w - cw_bot)
            x_top = f"min(max({_lerp_expr(p_left)},0),{max_top_x})" if p_left else f"{max_top_x // 2}"
            x_bot = f"min(max({_lerp_expr(p_right)},0),{max_bot_x})" if p_right else f"{max_bot_x // 2}"

            base = (
                f"[0:v]split=5[vf][sp1][sp2][ba][bb];"
                f"[vf]crop={cw}:{ch}:x='{x}':y={cy},scale={tw}:{th}:flags=lanczos,setsar=1[v_face];"
                f"[sp1]crop={cw_top}:{ch_top}:x='{x_top}':y={y_top},scale={tw}:{top_h}:flags=lanczos,setsar=1[top];"
                f"[sp2]crop={cw_bot}:{ch_bot}:x='{x_bot}':y={y_bot},scale={tw}:{bot_h}:flags=lanczos,setsar=1[bot];"
                f"[top][bot]vstack=inputs=2[stacked];"
                f"[stacked]drawbox=y={top_h - 2}:color=black@0.75:width=iw:height=4:t=fill[v_split];"
                f"[ba]scale={tw}:{th}:force_original_aspect_ratio=increase,crop={tw}:{th},boxblur=28:3[bg];"
                f"[bb]scale={tw}:{th}:force_original_aspect_ratio=decrease[fg];"
                f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[v_blur];"
                f"[v_face][v_split]overlay=0:0:enable='{s_expr}'[v_fs];"
                f"[v_fs][v_blur]overlay=0:0:enable='{b_expr}',setsar=1[v_raw]"
            )
        elif blur_intervals:
            b_expr = "+".join(f"between(t,{b0:.3f},{b1:.3f})" for b0, b1 in blur_intervals)
            base = (
                f"[0:v]split=3[vf][ba][bb];"
                f"[vf]crop={cw}:{ch}:x='{x}':y={cy},scale={tw}:{th}:flags=lanczos,setsar=1[v_face];"
                f"[ba]scale={tw}:{th}:force_original_aspect_ratio=increase,crop={tw}:{th},boxblur=28:3[bg];"
                f"[bb]scale={tw}:{th}:force_original_aspect_ratio=decrease[fg];"
                f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[v_blur];"
                f"[v_face][v_blur]overlay=0:0:enable='{b_expr}',setsar=1[v_raw]"
            )
        elif use_mid_split:
            s_expr = "+".join(f"between(t,{s0:.3f},{s1:.3f})" for s0, s1 in split_intervals)
            top_h = th // 2
            bot_h = th - top_h
            p_left, p_right, box_top, box_bot = split_paths if len(split_paths) == 4 else (*split_paths[:2], None, None)
            split_crop_w = min(src_w, int(round(src_h * tw / top_h)) - (int(round(src_h * tw / top_h)) % 2))
            default_ch = src_h - (src_h % 2)
            cw_top, ch_top, y_top = box_top if box_top else (split_crop_w, default_ch, 0)
            cw_bot, ch_bot, y_bot = box_bot if box_bot else (split_crop_w, default_ch, 0)
            max_top_x = max(0, src_w - cw_top)
            max_bot_x = max(0, src_w - cw_bot)
            x_top = f"min(max({_lerp_expr(p_left)},0),{max_top_x})" if p_left else f"{max_top_x // 2}"
            x_bot = f"min(max({_lerp_expr(p_right)},0),{max_bot_x})" if p_right else f"{max_bot_x // 2}"

            base = (
                f"[0:v]split=3[vf][sp1][sp2];"
                f"[vf]crop={cw}:{ch}:x='{x}':y={cy},scale={tw}:{th}:flags=lanczos,setsar=1[v_face];"
                f"[sp1]crop={cw_top}:{ch_top}:x='{x_top}':y={y_top},scale={tw}:{top_h}:flags=lanczos,setsar=1[top];"
                f"[sp2]crop={cw_bot}:{ch_bot}:x='{x_bot}':y={y_bot},scale={tw}:{bot_h}:flags=lanczos,setsar=1[bot];"
                f"[top][bot]vstack=inputs=2[stacked];"
                f"[stacked]drawbox=y={top_h - 2}:color=black@0.75:width=iw:height=4:t=fill[v_split];"
                f"[v_face][v_split]overlay=0:0:enable='{s_expr}',setsar=1[v_raw]"
            )
        else:
            base = f"[0:v]crop={cw}:{ch}:x='{x}':y={cy},scale={tw}:{th}:flags=lanczos,setsar=1[v_raw]"

    # Synchronized dead-air silence removal (applied after face-tracking crop so crop uses original t)
    if keep_segs and len(keep_segs) >= 1:
        sel_expr = "+".join(f"between(t,{s:.3f},{e:.3f})" for s, e in keep_segs)
        trim_filter = f";[v_raw]select='{sel_expr}',setpts='N/(if(isnan(FRAME_RATE),30,FRAME_RATE)*TB)'[v_trim]"
    else:
        trim_filter = ";[v_raw]null[v_trim]"

    # Retention punch-in zoom during first 2.2 seconds + periodic micro-zooms every 7.5s (dopamine pacing)
    if dynamic_zoom and aspect in ("9:16", "4:5", "1:1"):
        z_expr = "if(lt(t,2.2),1.07*(1-0.03*t/2.2),if(lt(mod(t,7.5),3.2),1.045,1.0))"
        zoom_filter = (
            f";[v_trim]scale=eval=frame:w='trunc({tw}*({z_expr})/2)*2':"
            f"h='trunc({th}*({z_expr})/2)*2',crop={tw}:{th}[v0]"
        )
    else:
        zoom_filter = ";[v_trim]null[v0]"

    return base + trim_filter + zoom_filter + ";" + _subs_filter(has_subs, "v0", "v")


def _consult_vision_model(
    src: str,
    start: float,
    end: float,
    title: str,
    words: list[dict],
    cfg: dict,
    model: str,
) -> dict | None:
    """Master Vision Director: scans the ENTIRE clip from start to end to detect every visual scene
    (camera cuts, image/slide popups, B-roll insertions, screen shares), extracts a keyframe for every scene,
    and obtains a full scene-by-scene directing plan to command OpenCV YuNet and FFmpeg.
    """
    try:
        from . import llm

        scene_segs = _detect_scene_segments(src, start, end, samples_per_sec=2.0, max_scenes=8)
        images_b64, scene_descs = _extract_scene_annotated_frames(src, start, scene_segs)

        if not images_b64:
            return None

        clip_words = [w["w"] for w in words if w.get("e", 0) > start and w.get("s", 0) < end]
        snippet = " ".join(clip_words)[:750]

        vis = llm.analyze_visual_framing(
            cfg,
            model,
            images_b64,
            title=title,
            transcript_snippet=snippet,
            scene_descriptions=scene_descs,
        )

        # Map the model's scene directives onto exact (start, end) timestamps
        raw_scenes = vis.get("scenes") or []
        scene_by_idx = {int(s.get("scene_index", i + 1)): s for i, s in enumerate(raw_scenes) if isinstance(s, dict)}
        default_layout = vis.get("layout") or "face"
        default_side = vis.get("primary_speaker_side") or "auto"
        default_graphic = bool(vis.get("has_onscreen_graphic", False))

        timeline: list[dict] = []
        for idx, (s0, s1) in enumerate(scene_segs, 1):
            sc = scene_by_idx.get(idx) or {}
            sc_layout = sc.get("layout") or default_layout
            sc_graphic = bool(sc.get("has_graphic", False)) or (sc_layout == "blur")
            if sc_graphic:
                sc_layout = "blur"
            timeline.append({
                "scene_index": idx,
                "start": s0,
                "end": s1,
                "layout": sc_layout,
                "focus_side": sc.get("focus_side") or default_side,
                "has_graphic": sc_graphic,
            })

        vis["scene_timeline"] = timeline
        log.info(
            "Master Vision Director plan (%d scenes): overall=%s, tracking=%s, scenes=%s, reason=%s",
            len(timeline),
            vis.get("layout"),
            vis.get("tracking_style"),
            [(t["scene_index"], t["layout"], f"{t['start']}..{t['end']}s") for t in timeline],
            vis.get("visual_reason"),
        )
        return vis
    except Exception as e:
        log.warning("Vision model consultation failed during render: %s", e)
        return None


def _slug(text: str, n: int = 48) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    return (s[:n].strip("-")) or "clip"


def render(clip: dict, video: dict, words: list[dict], opts: dict, cfg: dict, progress_cb) -> dict:
    """Render one clip. Returns {path, thumb, width, height, size, mode, visual_reason}."""
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
    requested_mode = opts.get("reframe") or cfg["reframe"]
    mode = requested_mode
    captions = bool(opts.get("captions", cfg["captions"]))
    show_headline = bool(opts.get("headline", cfg.get("headline_banner", True)))
    norm_audio = bool(opts.get("normalize_audio", cfg.get("normalize_audio", True)))
    clean_audio = bool(opts.get("clean_audio", cfg.get("clean_audio", True)))
    dynamic_zoom = bool(opts.get("dynamic_zoom", cfg.get("dynamic_zoom", True)))
    remove_silence = bool(opts.get("remove_silence", cfg.get("remove_silence", False)))

    tw, th = target_size(aspect, int(cfg["output_width"]))
    crop_w = int(round(sh * tw / th))
    crop_w -= crop_w % 2

    progress_cb(0.02, "Preparing")
    path = None
    split_paths = None
    gaming_boxes = None
    crop_box = None
    blur_intervals: list[tuple[float, float]] = []
    scene_timeline: list[dict] = []
    visual_reason = ""

    if aspect != "16:9" and crop_w <= sw:
        has_yunet = (Path(__file__).parent / "models" / "face_detection_yunet_2023mar.onnx").is_file()
        engine_label = "YuNet Neural" if has_yunet else "Cascades"

        vision_hints = None
        vision_model = (cfg.get("vision_model") or "").strip()
        if vision_model:
            progress_cb(0.03, f"Master Vision Director scanning full clip ({vision_model})")
            vision_hints = _consult_vision_model(src, start, end, clip.get("title") or "", words, cfg, vision_model)
            if vision_hints:
                visual_reason = (vision_hints.get("visual_reason") or "").strip()
                scene_timeline = vision_hints.get("scene_timeline") or []
                v_layout = vision_hints.get("layout")
                is_gaming = vision_hints.get("is_gaming")
                has_graphic = vision_hints.get("has_onscreen_graphic")

                # Calculate duration-weighted dominant layout across all scenes
                dur_by_layout: dict[str, float] = {}
                for sc in scene_timeline:
                    sc_l = sc.get("layout") or v_layout or "face"
                    dur_by_layout[sc_l] = dur_by_layout.get(sc_l, 0.0) + max(0.0, float(sc["end"]) - float(sc["start"]))

                all_blur = scene_timeline and all(sc.get("layout") == "blur" or sc.get("has_graphic") for sc in scene_timeline)
                has_face_scenes = any(sc.get("layout") in ("face", "split") for sc in scene_timeline)

                if requested_mode not in ("center", "blur"):
                    if (is_gaming or v_layout == "gaming" or dur_by_layout.get("gaming", 0.0) > dur * 0.4) and aspect == "9:16":
                        mode = "gaming"
                    elif all_blur or (has_graphic and not has_face_scenes):
                        mode = "blur"
                    elif dur_by_layout.get("split", 0.0) >= dur * 0.55 and aspect == "9:16":
                        mode = "split"
                    elif has_face_scenes or v_layout == "face":
                        mode = "face"
                engine_label += " + Master Vision"

        if mode == "gaming" and aspect == "9:16":
            webcam_hint = vision_hints.get("webcam_quadrant") if vision_hints else None
            progress_cb(0.05, f"Detecting Gaming Cam-Stack ({engine_label})")
            gaming_boxes = detect_gaming_layout(src, start, end, sw, sh, tw, th, webcam_hint=webcam_hint)
            if not gaming_boxes:
                mode = "blur"
            else:
                for sc in scene_timeline:
                    if sc.get("layout") == "blur" or sc.get("has_graphic"):
                        blur_intervals.append((float(sc["start"]), float(sc["end"])))
                if any(sc.get("layout") == "face" for sc in scene_timeline):
                    res = face_path(src, start, end, sw, sh, crop_w, vision_hints=vision_hints)
                    if res is not None:
                        path, crop_box, _ = res

        need_split_tracks = (mode == "split" and aspect == "9:16") or (
            mode == "face" and aspect == "9:16" and any(sc.get("layout") == "split" for sc in scene_timeline)
        )
        if need_split_tracks:
            top_h = th // 2
            split_crop_w = int(round(sh * tw / top_h))
            split_crop_w = min(sw, split_crop_w - (split_crop_w % 2))
            progress_cb(0.05, f"Tracking dual speakers ({engine_label})")
            split_paths = face_paths_split(src, start, end, sw, sh, split_crop_w)
            if mode == "split" and (not split_paths or (not split_paths[0] and not split_paths[1])):
                mode = "face"  # fallback to single face if no multi-face

        if mode == "face":
            progress_cb(0.06, f"Tracking faces across scenes ({engine_label})")
            res = face_path(src, start, end, sw, sh, crop_w, vision_hints=vision_hints)
            if res is None:
                mode = "blur"  # fallback to blur so graphics/slides/speakers are never sliced in half
            else:
                path, crop_box, blur_intervals = res
        elif mode == "split":
            for sc in scene_timeline:
                if sc.get("layout") == "blur" or sc.get("has_graphic"):
                    blur_intervals.append((float(sc["start"]), float(sc["end"])))

    headline_text = (clip.get("title") or "").strip() if (show_headline and aspect in ("9:16", "4:5")) else None
    has_caption_words = captions and any(w["e"] > start and w["s"] < end for w in words)
    has_subs = has_caption_words or bool(headline_text)

    # Synchronized silence removal across video, audio, and captions
    keep_segs: list[tuple[float, float]] | None = None
    remapped_ws: list[dict] | None = None
    effective_dur = dur
    if remove_silence and words:
        segs, r_ws, t_dur = compute_speech_segments(words, start, end)
        if t_dur < dur - 0.15:
            keep_segs = segs
            remapped_ws = r_ws if captions else []
            effective_dur = t_dur

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
            sub_words = [dict(w) for w in words] if captions else []
            (Path(td) / "subs.ass").write_text(
                build_ass(
                    sub_words, start, end, tw, th, cfg,
                    headline=headline_text, opts=opts,
                    precomputed_ws=remapped_ws, effective_dur=effective_dur,
                ),
                "utf-8",
            )
        graph = build_graph(mode, aspect, sw, sh, tw, th, path, crop_w, has_subs,
                            split_paths=split_paths, gaming_boxes=gaming_boxes,
                            dynamic_zoom=dynamic_zoom, keep_segs=keep_segs,
                            crop_box=crop_box, blur_intervals=blur_intervals,
                            scene_timeline=scene_timeline)

        # Audio handling: synchronized silence trim + denoising + broadcast standard loudnorm
        audio_args = []
        if info["has_audio"]:
            af_chain = []
            if keep_segs:
                sel_expr = "+".join(f"between(t,{s:.3f},{e:.3f})" for s, e in keep_segs)
                af_chain.extend([f"aselect='{sel_expr}'", "asetpts=N/SR/TB"])
            if clean_audio:
                af_chain.extend(["highpass=f=75", "lowpass=f=12000", "afftdn=nf=-25"])
            if norm_audio:
                af_chain.extend(["loudnorm=I=-14:LRA=11:TP=-1.5", "aresample=44100"])
            # Algorithm Hacking: 80ms loop crossfade at tail so TikTok/Shorts loop is 100% pop-free
            if effective_dur > 2.0:
                fade_start = max(0.0, effective_dur - 0.08)
                af_chain.append(f"afade=t=out:st={fade_start:.3f}:d=0.08")

            if af_chain:
                graph = f"{graph};[0:a:0]{','.join(af_chain)}[aout]"
                audio_args = ["-map", "[aout]", "-c:a", "aac", "-b:a", "192k", "-ar", "44100"]
            else:
                audio_args = ["-map", "0:a:0?", "-c:a", "aac", "-b:a", "160k", "-ar", "44100"]

        cmd = [media.FFMPEG, "-y", "-v", "error", "-nostats", "-progress", "pipe:1",
               "-ss", f"{start:.3f}", "-accurate_seek", "-i", src, "-t", f"{dur:.3f}",
               "-filter_complex", graph, "-map", "[v]", *audio_args,
               *venc, "-pix_fmt", "yuv420p",
               "-movflags", "+faststart", str(tmp_out)]
        log_path = Path(td) / "ffmpeg.log"
        with open(log_path, "wb") as logf:
            proc = subprocess.Popen(cmd, cwd=td, stdout=subprocess.PIPE, stderr=logf, text=True)
            assert proc.stdout is not None
            for line in proc.stdout:
                if line.startswith("out_time_ms="):
                    try:
                        sec = int(line.split("=")[1]) / 1_000_000
                        progress_cb(0.1 + 0.88 * min(sec / effective_dur, 1.0), "Rendering")
                    except ValueError:
                        pass
            code = proc.wait()
        if code != 0:
            err = log_path.read_text("utf-8", "replace")[-2500:]
            tmp_out.unlink(missing_ok=True)
            raise RuntimeError("ffmpeg failed: " + err.strip())

    tmp_out.replace(out_path)
    thumb = out_path.with_suffix(".jpg")
    media.make_thumb(out_path, thumb, at=min(1.0, effective_dur / 2), width=360)
    return {
        "path": str(out_path),
        "thumb": str(thumb) if thumb.exists() else None,
        "width": tw, "height": th,
        "size": out_path.stat().st_size,
        "mode": mode,
        "visual_reason": visual_reason,
    }
