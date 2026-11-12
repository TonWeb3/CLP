"""Cut a clip, reframe it, burn in captions, and encode it with ffmpeg."""
from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path

from .. import config
from . import media

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
def _rdp(points: list[tuple[float, float]], eps: float) -> list[tuple[float, float]]:
    if len(points) < 3:
        return points
    (x1, y1), (x2, y2) = points[0], points[-1]
    best, idx = 0.0, 0
    for i in range(1, len(points) - 1):
        x0, y0 = points[i]
        t = (x0 - x1) / (x2 - x1) if x2 != x1 else 0
        d = abs(y0 - (y1 + t * (y2 - y1)))
        if d > best:
            best, idx = d, i
    if best > eps:
        return _rdp(points[: idx + 1], eps)[:-1] + _rdp(points[idx:], eps)
    return [points[0], points[-1]]


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


def _detect_speaker_cands(cv2, gray, scale, frontal, profile, body) -> list[tuple[float, float, float]]:
    """Detect frontal faces, profile faces (both left and right via horizontal flip),
    and upper bodies (when head is turned/tilted). Returns [(cx, cy, area)].
    """
    cands: list[tuple[float, float, float]] = []

    # 1. Frontal face detection
    if frontal is not None:
        f_faces = frontal.detectMultiScale(
            gray, scaleFactor=1.1, minNeighbors=5,
            minSize=(max(24, int(32 * scale)), max(24, int(32 * scale)))
        )
        for (x, y, w, h) in f_faces:
            cands.append(((x + w / 2) / scale, (y + h / 2) / scale, float(w * h)))

    # 2. Profile face detection (left-facing)
    if profile is not None:
        p_faces = profile.detectMultiScale(
            gray, scaleFactor=1.1, minNeighbors=4,
            minSize=(max(24, int(32 * scale)), max(24, int(32 * scale)))
        )
        for (x, y, w, h) in p_faces:
            cands.append(((x + w / 2) / scale, (y + h / 2) / scale, float(w * h)))

        # 3. Profile face detection (right-facing via horizontal flip)
        flipped = cv2.flip(gray, 1)
        p_faces_flip = profile.detectMultiScale(
            flipped, scaleFactor=1.1, minNeighbors=4,
            minSize=(max(24, int(32 * scale)), max(24, int(32 * scale)))
        )
        img_w = gray.shape[1]
        for (x, y, w, h) in p_faces_flip:
            orig_x = img_w - (x + w)
            cands.append(((orig_x + w / 2) / scale, (y + h / 2) / scale, float(w * h)))

    # 4. Upper body detection (head + shoulders when speaker looks down or faces away)
    if not cands and body is not None:
        bodies = body.detectMultiScale(
            gray, scaleFactor=1.15, minNeighbors=3,
            minSize=(max(45, int(60 * scale)), max(45, int(60 * scale)))
        )
        for (x, y, w, h) in bodies:
            cands.append(((x + w / 2) / scale, (y + h * 0.35) / scale, float(w * h * 0.4)))

    return cands


def face_path(src: str, start: float, end: float, src_w: int, src_h: int, crop_w: int,
              samples_per_sec: float = 4.0) -> list[tuple[float, float]] | None:
    """Return [(t, crop_x)] with t relative to the clip start, or None if no speaker was found.
    Equipped with Active Speaker Auto-Cameraman: when multiple speakers are present (e.g. in podcasts
    or interview setups), detects mouth motion energy and fluidly pans to whoever is talking.
    """
    try:
        import cv2
    except ImportError:
        return None

    frontal, profile, body = _load_cascades(cv2)
    if not frontal and not profile and not body:
        return None

    cap = cv2.VideoCapture(src)
    if not cap.isOpened():
        return None

    det_w = 640
    scale = det_w / src_w if src_w > det_w else 1.0
    raw: list[tuple[float, float | None]] = []
    t = start
    step = 1.0 / samples_per_sec
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frame_interval = max(1, int(round(fps / samples_per_sec)))
    cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, start * 1000))
    prev_cx = None
    lost_count = 0
    mid_x = src_w / 2
    prev_gray = None

    # Active Speaker Motion state
    ema_l_motion = 0.0
    ema_r_motion = 0.0
    active_side = None
    side_switch_hold = 0

    try:
        while t < end:
            ok, frame = cap.read()
            if not ok:
                break
            small = cv2.resize(frame, None, fx=scale, fy=scale) if scale != 1.0 else frame
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            cands = _detect_speaker_cands(cv2, gray, scale, frontal, profile, body)
            cx = None
            if cands:
                left_cands = [c for c in cands if c[0] < mid_x]
                right_cands = [c for c in cands if c[0] >= mid_x]

                if left_cands and right_cands:
                    best_l = max(left_cands, key=lambda c: c[2])
                    best_r = max(right_cands, key=lambda c: c[2])

                    m_l, m_r = 0.0, 0.0
                    if prev_gray is not None:
                        for spk, (ccx, ccy, _) in (("l", best_l), ("r", best_r)):
                            sx, sy = int(ccx * scale), int(ccy * scale)
                            my = max(0, min(gray.shape[0] - 15, sy + 4))
                            mx = max(0, min(gray.shape[1] - 24, sx - 12))
                            diff = float(cv2.absdiff(gray[my:my+15, mx:mx+24], prev_gray[my:my+15, mx:mx+24]).mean())
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
                        if side_switch_hold >= 5 or active_side is None:
                            active_side = desired_side
                            side_switch_hold = 0
                    else:
                        side_switch_hold = 0

                    chosen = best_l if active_side == "left" else best_r
                    cx = chosen[0]
                    prev_cx = cx
                    lost_count = 0
                else:
                    if prev_cx is None:
                        cx = max(cands, key=lambda c: c[2])[0]
                        prev_cx = cx
                        lost_count = 0
                    else:
                        near = [c for c in cands if abs(c[0] - prev_cx) < crop_w * 0.6]
                        if near:
                            cx = min(near, key=lambda c: abs(c[0] - prev_cx))[0]
                            prev_cx = cx
                            lost_count = 0
                        else:
                            lost_count += 1
                            if lost_count >= 5:  # ~1.25s sustained absence -> genuine shot/speaker cut
                                cx = max(cands, key=lambda c: c[2])[0]
                                prev_cx = cx
                                lost_count = 0
                            else:
                                # Hold previous position; don't whip across the screen for 1 dropped frame
                                cx = prev_cx

            prev_gray = gray
            raw.append((t - start, cx))
            t += step
            for _ in range(frame_interval - 1):
                if not cap.grab():
                    break
    finally:
        cap.release()

    valid = [(tt, c) for tt, c in raw if c is not None]
    if not valid:
        return None

    # Calculate median detected speaker position as the robust anchor
    sorted_valid_x = sorted([c for _, c in valid])
    anchor_cx = sorted_valid_x[len(sorted_valid_x) // 2]

    # Fill missing frames: if speaker is momentarily occluded or looks down,
    # stay locked on their known position instead of jumping to dead-center empty space!
    filled: list[tuple[float, float]] = []
    last = valid[0][1] if valid else anchor_cx
    for tt, c in raw:
        if c is not None:
            last = c
        filled.append((tt, last))

    return _build_cinematic_path(filled, crop_w, src_w, end - start)


def _build_cinematic_path(raw_points: list[tuple[float, float]], crop_w: int, src_w: int, duration: float) -> list[tuple[float, float]]:
    """Convert raw face tracking points into a stabilized cinematic camera track.
    Locks rock-solid like a tripod on stationary speakers (eliminating micro-jitter on head nods),
    and executes fluid, eased pans only when a speaker genuinely moves or changes seats.
    """
    if not raw_points:
        return []
    half = crop_w / 2
    max_x = max(0, src_w - crop_w)

    # 1. Median filter over ~1.5s window to remove brief detection drops/flickers
    xs = [p[1] for p in raw_points]
    n = len(xs)
    med = []
    for i in range(n):
        win = sorted(xs[max(0, i - 3): min(n, i + 4)])
        med.append(win[len(win) // 2])

    # 2. Exponential Moving Average (alpha=0.18) for gentle baseline smoothing
    ema = [med[0]]
    alpha = 0.18
    for val in med[1:]:
        ema.append(alpha * val + (1.0 - alpha) * ema[-1])

    # 3. Tripod deadband (22% of crop width)
    # The camera stays 100% stationary unless the speaker's center drifts past this threshold
    deadband = crop_w * 0.22
    cam_center = ema[0]
    initial_crop_x = min(max(cam_center - half, 0), max_x)
    anchors = [(0.0, round(initial_crop_x, 1))]

    i = 0
    while i < n:
        tt = raw_points[i][0]
        target = ema[i]
        diff = target - cam_center

        if abs(diff) > deadband:
            # The speaker has sustained a new position outside the deadband!
            # Move camera to new center over a smooth 1.2s fluid pan
            pan_duration = 1.2
            pan_start_t = max(0.0, tt)
            pan_end_t = min(round(duration, 3), round(pan_start_t + pan_duration, 3))
            if pan_end_t <= pan_start_t:
                pan_end_t = min(round(duration, 3), round(pan_start_t + 0.5, 3))

            new_target = target
            new_crop_x = min(max(new_target - half, 0), max_x)

            # Anchor at start of pan (holds previous position until pan starts)
            prev_crop_x = anchors[-1][1]
            if pan_start_t > anchors[-1][0] + 0.15:
                anchors.append((round(pan_start_t, 3), prev_crop_x))

            # Anchor at end of pan (completes fluid move to new target)
            anchors.append((round(pan_end_t, 3), round(new_crop_x, 1)))
            cam_center = new_target

            # Fast-forward past the pan duration
            while i < n and raw_points[i][0] < pan_end_t:
                i += 1
            continue
        i += 1

    # Ensure final anchor reaches the end of the clip
    if anchors[-1][0] < duration:
        anchors.append((round(duration, 3), anchors[-1][1]))

    return anchors


def face_paths_split(src: str, start: float, end: float, src_w: int, src_h: int, crop_w: int,
                     samples_per_sec: float = 4.0) -> tuple[list[tuple[float, float]] | None, list[tuple[float, float]] | None]:
    """Return two paths [(t, crop_x)] for Speaker 1 (top/left) and Speaker 2 (bottom/right)."""
    try:
        import cv2
    except ImportError:
        return None, None

    frontal, profile, body = _load_cascades(cv2)
    if not frontal and not profile and not body:
        return None, None

    cap = cv2.VideoCapture(src)
    if not cap.isOpened():
        return None, None

    det_w = 640
    scale = det_w / src_w if src_w > det_w else 1.0
    t = start
    step = 1.0 / samples_per_sec
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frame_interval = max(1, int(round(fps / samples_per_sec)))
    cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, start * 1000))

    raw_left: list[tuple[float, float | None]] = []
    raw_right: list[tuple[float, float | None]] = []
    mid_x = src_w / 2

    try:
        while t < end:
            ok, frame = cap.read()
            if not ok:
                break
            small = cv2.resize(frame, None, fx=scale, fy=scale) if scale != 1.0 else frame
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            cands = _detect_speaker_cands(cv2, gray, scale, frontal, profile, body)
            cx_left, cx_right = None, None

            if cands:
                left_cands = [c for c in cands if c[0] < mid_x]
                right_cands = [c for c in cands if c[0] >= mid_x]

                if left_cands and right_cands:
                    cx_left = max(left_cands, key=lambda c: c[2])[0]
                    cx_right = max(right_cands, key=lambda c: c[2])[0]
                elif len(cands) >= 2:
                    sorted_c = sorted(cands, key=lambda c: c[0])
                    cx_left = sorted_c[0][0]
                    cx_right = sorted_c[-1][0]
                elif left_cands:
                    cx_left = left_cands[0][0]
                elif right_cands:
                    cx_right = right_cands[0][0]

            raw_left.append((t - start, cx_left))
            raw_right.append((t - start, cx_right))
            t += step
            for _ in range(frame_interval - 1):
                if not cap.grab():
                    break
    finally:
        cap.release()

    def process_raw(raw, default_anchor):
        valid = [(tt, c) for tt, c in raw if c is not None]
        anchor = sorted([c for _, c in valid])[len(valid) // 2] if valid else default_anchor
        filled: list[tuple[float, float]] = []
        last = valid[0][1] if valid else anchor
        for tt, c in raw:
            if c is not None:
                last = c
            filled.append((tt, last))
        return _build_cinematic_path(filled, crop_w, src_w, end - start)

    has_left = any(c is not None for _, c in raw_left)
    has_right = any(c is not None for _, c in raw_right)
    if not has_left and not has_right:
        return None, None

    default_left = min(crop_w * 0.5, mid_x * 0.5)
    default_right = max(src_w - crop_w * 0.5, mid_x + (src_w - mid_x) * 0.5)

    path_left = process_raw(raw_left, default_left)
    path_right = process_raw(raw_right, default_right)
    return path_left, path_right


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
        opt = f":fontsdir='{esc_dir}'"
    return f"[{in_label}]ass=subs.ass{opt}[{out_label}]"


def build_graph(mode: str, aspect: str, src_w: int, src_h: int, tw: int, th: int,
                path: list[tuple[float, float]] | None, crop_w: int, has_subs: bool,
                split_paths: tuple | None = None, dynamic_zoom: bool = True,
                keep_segs: list[tuple[float, float]] | None = None) -> str:
    """Return a -filter_complex graph taking [0:v] and producing [v]."""
    if aspect == "16:9":
        base = (f"[0:v]scale={tw}:{th}:force_original_aspect_ratio=decrease,"
                f"pad={tw}:{th}:(ow-iw)/2:(oh-ih)/2,setsar=1[v_raw]")
    elif mode == "split" and aspect == "9:16":
        # Two stacked frames: Top speaker + Bottom speaker
        top_h = th // 2
        bot_h = th - top_h
        split_crop_w = int(round(src_h * tw / top_h))
        split_crop_w = min(src_w, split_crop_w - (split_crop_w % 2))
        cw = split_crop_w
        ch = src_h - (src_h % 2)
        max_split_x = max(0, src_w - cw)

        p_left, p_right = split_paths if split_paths else (None, None)
        x_top = f"min(max({_lerp_expr(p_left)},0),{max_split_x})" if p_left else f"{max(0, min(max_split_x, int(round(src_w * 0.25 - cw / 2))))}"
        x_bot = f"min(max({_lerp_expr(p_right)},0),{max_split_x})" if p_right else f"{max(0, min(max_split_x, int(round(src_w * 0.75 - cw / 2))))}"

        base = (
            f"[0:v]split=2[sp1][sp2];"
            f"[sp1]crop={cw}:{ch}:x='{x_top}':y=0,scale={tw}:{top_h}:flags=lanczos,setsar=1[top];"
            f"[sp2]crop={cw}:{ch}:x='{x_bot}':y=0,scale={tw}:{bot_h}:flags=lanczos,setsar=1[bot];"
            f"[top][bot]vstack=inputs=2[stacked];"
            f"[stacked]drawbox=y={top_h - 2}:color=black@0.75:width=iw:height=4:t=fill[v_raw]"
        )
    elif mode == "blur" or crop_w > src_w:
        base = (f"[0:v]split=2[a][b];"
                f"[a]scale={tw}:{th}:force_original_aspect_ratio=increase,crop={tw}:{th},boxblur=28:3[bg];"
                f"[b]scale={tw}:{th}:force_original_aspect_ratio=decrease[fg];"
                f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[v_raw]")
    else:
        cw = crop_w - (crop_w % 2)
        ch = src_h - (src_h % 2)
        max_x = max(0, src_w - cw)
        if mode == "face" and path:
            x = f"min(max({_lerp_expr(path)},0),{max_x})"
        else:
            x = f"{max_x // 2}"
        base = f"[0:v]crop={cw}:{ch}:x='{x}':y=0,scale={tw}:{th}:flags=lanczos,setsar=1[v_raw]"

    # Synchronized dead-air silence removal (applied after face-tracking crop so crop uses original t)
    if keep_segs and len(keep_segs) >= 1:
        sel_expr = "+".join(f"between(t,{s:.3f},{e:.3f})" for s, e in keep_segs)
        trim_filter = f";[v_raw]select='{sel_expr}',setpts=N/FRAME_RATE/TB[v_trim]"
    else:
        trim_filter = ";[v_raw]null[v_trim]"

    # Retention punch-in zoom during first 2.2 seconds + periodic micro-zooms every 7.5s (dopamine pacing)
    if dynamic_zoom and aspect in ("9:16", "4:5", "1:1"):
        z_expr = "if(lt(t,2.2),1.07*(1-0.03*t/2.2),if(lt(mod(t,7.5),3.2),1.045,1.0))"
        zoom_filter = (
            f";[v_trim]scale=eval=frame:w='{tw}*({z_expr})':"
            f"h='{th}*({z_expr})',crop={tw}:{th}[v0]"
        )
    else:
        zoom_filter = ";[v_trim]null[v0]"

    return base + trim_filter + zoom_filter + ";" + _subs_filter(has_subs, "v0", "v")


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

    if aspect != "16:9" and crop_w <= sw:
        if mode == "split" and aspect == "9:16":
            top_h = th // 2
            split_crop_w = int(round(sh * tw / top_h))
            split_crop_w = min(sw, split_crop_w - (split_crop_w % 2))
            progress_cb(0.04, "Detecting speakers for split-screen")
            split_paths = face_paths_split(src, start, end, sw, sh, split_crop_w)
            if not split_paths or (not split_paths[0] and not split_paths[1]):
                mode = "face"  # fallback to single face if no multi-face
        if mode == "face":
            progress_cb(0.04, "Tracking faces")
            path = face_path(src, start, end, sw, sh, crop_w)
            if path is None:
                mode = "blur"  # fallback to blur so graphics/slides/speakers are never sliced in half

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
                            split_paths=split_paths, dynamic_zoom=dynamic_zoom, keep_segs=keep_segs)

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
                af_chain.append("loudnorm=I=-14:LRA=11:TP=-1.5")
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
    }
