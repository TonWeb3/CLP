"""Find the best moments in a transcript.

Pass 1 (filter model): read the transcript in chunks with numbered sentences and propose clips as
sentence ranges, so the model chooses the boundaries instead of code guessing.
Pass 2 (ranker model): compare the shortlist side by side and rank it.
Loudness is blended in as a small extra signal.
"""
from __future__ import annotations

import logging
import math
from pathlib import Path

from . import llm

log = logging.getLogger("clipforge.analyze")

PROPOSE_SCHEMA = {
    "type": "object",
    "properties": {
        "clips": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "start_sentence": {"type": "integer"},
                    "end_sentence": {"type": "integer"},
                    "hook_sentence": {"type": "integer"},
                    "loop_score": {"type": "number"},
                    "score": {"type": "number"},
                    "title": {"type": "string"},
                    "hook": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["start_sentence", "end_sentence", "score", "title", "hook", "reason"],
            },
        }
    },
    "required": ["clips"],
}

RANK_SCHEMA = {
    "type": "object",
    "properties": {
        "ranking": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "score": {"type": "number"},
                    "title": {"type": "string"},
                    "hook": {"type": "string"},
                    "reason": {"type": "string"},
                    "start_sentence": {"type": "integer"},
                    "end_sentence": {"type": "integer"},
                    "hook_sentence": {"type": "integer"},
                    "loop_score": {"type": "number"},
                },
                "required": ["id", "score", "title", "hook", "reason"],
            },
        }
    },
    "required": ["ranking"],
}

CRITERIA = """VIRAL SHORT-FORM RETENTION & ENGAGEMENT FRAMEWORK (TikTok, Shorts, Reels):

1. THE 3-SECOND RETENTION HOOK:
   - The opening line MUST trigger an immediate pattern interrupt or curiosity gap. Viewers decide to swipe within 2-3 seconds.
   - It should fit one of the 4 proven Viral Archetypes:
     * [Controversial] Challenges a common myth, popular belief, or status quo with a bold contrarian stance.
     * [Story/Confession] A dramatic personal turning point, failure, high-stakes moment, or shocking reveal.
     * [Actionable Insight] A practical "cheat code" or framework that gives viewers an immediate unfair advantage.
     * [Curiosity/Mystery] Poses an intriguing question or startling observation that compels viewers to watch till the end.
   - CRITICAL HOOK CLEANLINESS:
     * Identify `hook_sentence`: the EXACT sentence index where the core explosive hook/premise lands.
     * Strip out rambling preamble, small talk, agreement filler ("yeah exactly", "so um", "like I said", "you know").
     * NEVER start on a dangling pronoun ("He did this", "They said that") unless the person or subject was explicitly introduced in the opening line! A standalone viewer must understand who is speaking and who is being discussed.

2. COMPLETE INFORMATION & SELF-CONTAINED NARRATIVE:
   - The clip must deliver a complete, self-contained thought from start to finish.
   - Every clip must form a complete 3-part arc:
     1) The Hook / Premise (the question, controversy, or situation).
     2) The Mechanism / Story (the explanation, struggle, or proof).
     3) The Resolution / Lesson (the moral, payoff, or punchline).
   - If a premise or question is raised, the clip MUST include the full explanation and conclusion.
   - NEVER end on cliffhanger transition words ("because...", "and so...", "which means...", "that's why...", "like...").

3. SATISFYING PAYOFF & RETENTION:
   - Concludes on a memorable punchline, decisive realization, clear lesson, or natural resolution.
   - The ending must feel like a completed loop, leaving the viewer satisfied and eager to save or share.

4. SEAMLESS LOOP ENGINEERING (THE >100% RETENTION APV HACK):
   - Check if the concluding sentence naturally flows grammatically and contextually back into the opening hook sentence.
   - When the ending naturally loops into the beginning, viewers watch the first few seconds twice before noticing, pushing Average Percentage Viewed (APV) past 100%.
   - Score `loop_score` from 1 to 10 (10 = perfect continuous circular thought, e.g. End: "And that is the exact reason why..." -> Start: "...99% of people fail at business").

5. HIGH-CTR CURIOSITY-GAP TITLES:
   - Formatted like viral YouTube Shorts / TikTok headlines (high curiosity, strong contrast, under 60 characters).
   - Capitalize key words (e.g. "The Brutal Truth About Money", "Why 99% Of People Fail At X", "The 1 Habit That Changed Everything").

Avoid: Slow ramp-ups, clips cut off mid-thought, small talk, sponsor reads, and rambling without a payoff."""


def fmt_ts(sec: float) -> str:
    sec = int(max(0, sec))
    m, s = divmod(sec, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def make_chunks(sentences: list[dict], chunk_sec: float, overlap: int = 8) -> list[tuple[int, int]]:
    """Index ranges [i, j) covering the transcript, with a generous overlap between chunks."""
    chunks = []
    n = len(sentences)
    i = 0
    while i < n:
        j = i
        while j < n and sentences[j]["e"] - sentences[i]["s"] < chunk_sec:
            j += 1
        j = min(max(j, i + 1), n)
        chunks.append((i, j))
        if j >= n:
            break
        i = max(j - overlap, i + 1)
    return chunks


DANGLING_END_WORDS = {
    "because", "and", "but", "so", "which", "that", "like", "when", "if", "or",
    "to", "with", "than", "as", "since", "while", "where", "who", "whom", "whose",
    "then", "actually", "basically", "meaning", "into",
}


def _is_filler_sentence(text: str) -> bool:
    clean = text.lower().strip(".,!?:;\"'()[]{}- ")
    words = clean.split()
    if len(words) <= 3 and any(clean.startswith(p) for p in ("yeah", "yes", "right", "okay", "ok", "exactly", "totally", "sure", "i agree", "100%")):
        return True
    return False


def normalize_span(a: int, b: int, sentences: list[dict], lo: int, hi: int, min_s: float, max_s: float, hook_sentence: int | None = None):
    """Clamp a proposed sentence range into [lo, hi) ensuring complete thoughts, clean cuts,
    zero-second front-sliced hook snapping, and protecting the concluding punchline/payoff."""
    a = max(lo, min(a, hi - 1))
    b = max(lo, min(b, hi - 1))
    if b < a:
        a, b = b, a

    def dur(x, y):
        return sentences[y]["e"] - sentences[x]["s"]

    # Allow a small buffer (up to 10% over max_s) to preserve a complete final sentence/payoff
    effective_max = max_s * 1.10

    # 0. Front-Slice Hook Snapping: If a golden hook sentence is identified within the first 3 sentences
    # and starting there still meets min_clip_sec, snap directly to it (eliminating slow ramp-ups!)
    if hook_sentence is not None:
        hs = max(lo, min(hook_sentence, hi - 1))
        if a < hs <= min(a + 3, b) and dur(hs, b) >= min_s:
            a = hs

    # 1. Clean up conversational filler at the start
    while a < b and _is_filler_sentence(sentences[a]["text"]) and dur(a + 1, b) >= min_s:
        a += 1

    # 2. Check if the ending sentence ends on a dangling connector (e.g. "because", "and", "which")
    last_words = sentences[b]["text"].rstrip(".,!?:;\"'()[]{}- ").lower().split()
    if last_words and last_words[-1] in DANGLING_END_WORDS:
        # If the thought is incomplete, try extending to the next sentence if within time limit
        if b + 1 < hi and dur(a, b + 1) <= effective_max:
            b += 1
        elif (b - a) >= 2 and dur(a, b - 1) >= min_s:
            # Otherwise, snap back to the previous sentence if it was already complete
            b -= 1

    # 3. Payoff Protection: If clip exceeds effective_max, trim preamble from the FRONT first!
    while b > a and dur(a, b) > effective_max:
        # If trimming the opening sentence preserves >= min_s, trim from front so punchline is saved!
        if (b - a) >= 2 and dur(a + 1, b) >= min_s:
            a += 1
        else:
            b -= 1

    # 4. If too short, extend cleanly if the next sentence fits without overshooting
    while dur(a, b) < min_s and b + 1 < hi and dur(a, b + 1) <= effective_max:
        b += 1

    d = dur(a, b)
    if d < min_s * 0.7 or d > max_s * 1.18:
        return None
    return a, b


def _clip_text(sentences, a, b, limit=None):
    text = " ".join(s["text"] for s in sentences[a:b + 1])
    if limit and len(text) > limit:
        head = int(limit * 0.62)
        text = text[:head].rstrip() + " ... " + text[-(limit - head):].lstrip()
    return text


def propose(cfg, sentences, chunk, min_s, max_s, want):
    lo, hi = chunk
    lines = [f"[{s['i']}] {fmt_ts(s['s'])} {s['text']}" for s in sentences[lo:hi]]
    system = (
        "You are an elite short-form video editor specializing in viral clips for TikTok, YouTube Shorts, and Instagram Reels.\n\n"
        + CRITERIA +
        f"\n\nRules:\n- Target duration: each clip MUST last between {int(min_s)} and {int(max_s)} seconds. Check the timestamps!\n"
        f"- Completeness is mandatory: Choose `start_sentence` and `end_sentence` so the clip delivers a 100% complete thought. "
        "Do NOT cut off before the explanation or story finishes.\n"
        f"- Return at most {want} clips from this section. Return fewer, or none, if the section has no viral moments.\n"
        "- start_sentence: The sentence where the hook/topic starts cleanly.\n"
        "- end_sentence: The sentence where the idea concludes with a satisfying payoff.\n"
        "- hook_sentence: (Optional) The exact sentence number containing the explosive golden hook if it starts 1-2 lines after small talk.\n"
        "- loop_score: (Optional) 1-10 rating seamless loop flow from the end sentence into the start sentence.\n"
        "- title: Punchy, high-CTR viral title (under 60 characters, curiosity-driven, capitalize key words).\n"
        "- hook: The exact opening spoken line that grabs attention, copied from the transcript.\n"
        "- reason: Format as '[Archetype] Explanation' (e.g. '[Controversial] Debunks myth about X with proof').\n"
        "- score: 1-10 based on hook strength, viral potential, and complete payoff.\n"
        "- Clips must not overlap each other."
    )
    user = (f"Transcript section from {fmt_ts(sentences[lo]['s'])} to {fmt_ts(sentences[hi - 1]['e'])}. "
            "Each line is: [sentence number] timestamp text\n\n" + "\n".join(lines))
    data = llm.chat_json(cfg, cfg["_filter_model"], system, user, PROPOSE_SCHEMA)
    return data.get("clips") or []


def rank(cfg, sentences, cands, want):
    items = []
    for k, c in enumerate(cands, 1):
        dur = int(c["end"] - c["start"])
        items.append(f"ID {k} [sentences {c['a']}..{c['b']}] ({dur}s): {_clip_text(sentences, c['a'], c['b'], 700)}")
    system = (
        "You are the executive producer and final judge for a viral short-form channel. You are given candidate clips "
        "from one long video. Rank them by viral potential, retention, and completeness.\n\n" + CRITERIA +
        f"\n\nRules:\n"
        f"- Prioritize clips with an explosive 3-second hook, high retention potential, and a complete, satisfying payoff.\n"
        f"- Heavily penalize or discard any candidate that starts slowly, rambles, or feels cut off mid-thought.\n"
        f"- Boundary refinement: You may adjust `start_sentence` or `end_sentence` if needed (e.g. to trim leading host banter or include a missing concluding sentence). Otherwise keep the candidate's existing sentence range.\n"
        f"- Return the best {min(want, len(cands))} clips, best first. For each:\n"
        "  * id: as given\n"
        "  * score: 1-10 (differentiate viral home runs from average clips)\n"
        "  * title: an improved, curiosity-gap viral title (under 60 characters)\n"
        "  * hook: the opening spoken line\n"
        "  * reason: '[Archetype] Why this will hook viewers and retain them'\n"
        "  * start_sentence: starting sentence index (optional, integer)\n"
        "  * end_sentence: concluding sentence index (optional, integer)\n"
        "  * hook_sentence: index of the golden hook line if starting there eliminates preamble (optional, integer)\n"
        "  * loop_score: 1-10 rating seamless loop flow into the opening hook (optional, number)"
    )
    user = "Candidates:\n\n" + "\n\n".join(items)
    data = llm.chat_json(cfg, cfg["_ranker_model"], system, user, RANK_SCHEMA)
    return data.get("ranking") or []


def energy_scores(curve: list[float], cands: list[dict], step: float = 0.5) -> list[float]:
    """0..1 loudness score per candidate (mean of the loudest quarter, min-max scaled)."""
    raw = []
    for c in cands:
        seg = curve[int(c["start"] / step): int(c["end"] / step) + 1]
        if not seg:
            raw.append(0.0)
            continue
        seg = sorted(seg, reverse=True)
        top = seg[: max(1, len(seg) // 4)]
        raw.append(sum(top) / len(top))
    if not raw:
        return []
    lo, hi = min(raw), max(raw)
    if hi - lo < 1e-9:
        return [0.5] * len(raw)
    return [(r - lo) / (hi - lo) for r in raw]


def _overlap(a, b):
    inter = min(a["end"], b["end"]) - max(a["start"], b["start"])
    if inter <= 0:
        return 0.0
    return inter / min(a["end"] - a["start"], b["end"] - b["start"])


def find_clips(cfg: dict, sentences: list[dict], duration: float, curve: list[float], progress_cb, cancel_check, video_path: str | None = None) -> list[dict]:
    if not sentences:
        raise RuntimeError("No speech was found in this video.")
    cfg = dict(cfg)
    f_model = cfg.get("filter_model") or cfg.get("ranker_model")
    r_model = cfg.get("ranker_model") or cfg.get("filter_model")
    if not f_model:
        raise RuntimeError("No LLM model selected. Open the Models page, pick a model, and retry.")
    cfg["_filter_model"], cfg["_ranker_model"] = f_model, r_model

    min_s, max_s = float(cfg["min_clip_sec"]), float(cfg["max_clip_sec"])
    want = int(cfg["num_candidates"])
    chunks = make_chunks(sentences, float(cfg["chunk_minutes"]) * 60)
    per_chunk = max(2, math.ceil(want * 2 / len(chunks)))

    cands: list[dict] = []
    errors: list[str] = []
    for ci, chunk in enumerate(chunks):
        cancel_check()
        progress_cb(ci / (len(chunks) + 1), f"Finding moments (part {ci + 1} of {len(chunks)})")
        try:
            props = propose(cfg, sentences, chunk, min_s, max_s, per_chunk)
        except llm.LLMError as e:
            errors.append(str(e))
            log.warning("chunk %s failed: %s", ci, e)
            if len(errors) >= 2 and not cands:
                break  # no point continuing if the model is unreachable
            continue
        for p in props:
            hs = p.get("hook_sentence")
            try:
                span = normalize_span(int(p["start_sentence"]), int(p["end_sentence"]),
                                      sentences, chunk[0], chunk[1], min_s, max_s,
                                      hook_sentence=int(hs) if hs is not None else None)
            except (KeyError, TypeError, ValueError):
                continue
            if not span:
                continue
            a, b = span
            # Zero-second speech padding: hook starts speaking within 60-120ms of video start
            if a > 0:
                gap_before = max(0.0, sentences[a]["s"] - sentences[a - 1]["e"])
                pad_start = min(0.12, max(0.06, gap_before * 0.25))
            else:
                pad_start = 0.08

            if b + 1 < len(sentences):
                gap_after = max(0.0, sentences[b + 1]["s"] - sentences[b]["e"])
                pad_end = min(0.65, max(0.20, gap_after * 0.55))
            else:
                pad_end = 0.50

            clip_start = max(0.0, round(sentences[a]["s"] - pad_start, 2))
            clip_end = min(duration or 1e9, round(sentences[b]["e"] + pad_end, 2))

            loop_val = float(p.get("loop_score") or 0.0) if p.get("loop_score") is not None else 0.0
            cands.append({
                "a": a, "b": b,
                "start": clip_start,
                "end": clip_end,
                "p1": min(10.0, max(1.0, float(p.get("score") or 5))),
                "title": (p.get("title") or "").strip()[:100],
                "hook": (p.get("hook") or "").strip(),
                "reason": (p.get("reason") or "").strip(),
                "loop_score": loop_val,
            })

    if not cands:
        if errors:
            raise RuntimeError(errors[0])
        raise RuntimeError("The model did not find any clip-worthy moments. Try lowering the minimum clip length "
                           "or using a larger model.")

    # de-duplicate overlapping proposals, best first
    cands.sort(key=lambda c: c["p1"], reverse=True)
    kept: list[dict] = []
    for c in cands:
        if all(_overlap(c, k) < 0.2 for k in kept):
            kept.append(c)
    shortlist = kept[: min(40, max(want * 2, 12))]

    # pass 2: rank
    progress_cb(len(chunks) / (len(chunks) + 1), "Ranking the best moments")
    cancel_check()
    p2: dict[int, dict] = {}
    if len(shortlist) > 1 or True:
        try:
            for r in rank(cfg, sentences, shortlist, want):
                k = int(r["id"]) - 1
                if 0 <= k < len(shortlist) and k not in p2:
                    p2[k] = r
        except (llm.LLMError, KeyError, TypeError, ValueError) as e:
            log.warning("ranking failed, using pass-1 scores: %s", e)

    ew = float(cfg.get("energy_weight", 0.15))
    en = energy_scores(curve, shortlist) if curve else [0.5] * len(shortlist)
    scored = []
    for k, c in enumerate(shortlist):
        r = p2.get(k)
        if r:
            llm_score = 0.65 * min(10.0, max(1.0, float(r.get("score") or 5))) + 0.35 * c["p1"]
            c["title"] = (r.get("title") or c["title"]).strip()[:100] or c["title"]
            c["hook"] = (r.get("hook") or c["hook"]).strip() or c["hook"]
            c["reason"] = (r.get("reason") or c["reason"]).strip() or c["reason"]
            loop_val = float(r.get("loop_score") or c.get("loop_score") or 0.0)
            if loop_val >= 7.0:
                c["loop_score"] = loop_val
                if "Seamless Loop" not in c["reason"]:
                    c["reason"] = f"[🔄 Seamless Loop: {int(loop_val)}/10] " + c["reason"]
                llm_score += min(1.0, 0.25 * (loop_val - 6.0))
            # Apply ranker boundary refinement if provided
            if r.get("start_sentence") is not None and r.get("end_sentence") is not None:
                try:
                    ref_hs = r.get("hook_sentence")
                    ref_span = normalize_span(int(r["start_sentence"]), int(r["end_sentence"]),
                                              sentences, 0, len(sentences), min_s, max_s,
                                              hook_sentence=int(ref_hs) if ref_hs is not None else None)
                    if ref_span:
                        ra, rb = ref_span
                        c["a"], c["b"] = ra, rb
                        gap_before = max(0.0, sentences[ra]["s"] - sentences[ra - 1]["e"]) if ra > 0 else 0.12
                        pad_start = min(0.12, max(0.06, gap_before * 0.25)) if ra > 0 else 0.08
                        gap_after = max(0.0, sentences[rb + 1]["s"] - sentences[rb]["e"]) if rb + 1 < len(sentences) else 0.50
                        pad_end = min(0.65, max(0.20, gap_after * 0.55)) if rb + 1 < len(sentences) else 0.50
                        c["start"] = max(0.0, round(sentences[ra]["s"] - pad_start, 2))
                        c["end"] = min(duration or 1e9, round(sentences[rb]["e"] + pad_end, 2))
                except Exception:
                    pass
        else:
            llm_score = c["p1"] * (0.85 if p2 else 1.0)  # unranked candidates sit below ranked ones
        c["score"] = round((1 - ew) * llm_score + ew * en[k] * 10, 2)
        scored.append(c)
    scored.sort(key=lambda c: c["score"], reverse=True)
    top_clips = scored[:want]

    # Pass 3: Visual Director (Vision Model frame inspection & framing recommendation)
    vision_model = (cfg.get("vision_model") or "").strip()
    if vision_model and video_path and Path(video_path).exists():
        progress_cb(0.92, "Visual Director: inspecting frames & layout")
        cancel_check()
        try:
            import base64
            import tempfile
            from . import media

            with tempfile.TemporaryDirectory(prefix="clipforge-vis-") as td:
                for idx, c in enumerate(top_clips):
                    cancel_check()
                    # Sample 1 keyframe early in the hook, 1 in the middle
                    t_hook = max(0.0, float(c["start"]) + 0.8)
                    t_mid = (float(c["start"]) + float(c["end"])) / 2

                    images_b64: list[str] = []
                    for t_idx, at_sec in enumerate([t_hook, t_mid]):
                        thumb_file = Path(td) / f"vis_{idx}_{t_idx}.jpg"
                        if media.make_thumb(video_path, thumb_file, at=at_sec, width=640):
                            images_b64.append(base64.b64encode(thumb_file.read_bytes()).decode("utf-8"))

                    if images_b64:
                        try:
                            vis = llm.analyze_visual_framing(
                                cfg, vision_model, images_b64,
                                c["title"], _clip_text(sentences, c["a"], c["b"], 300)
                            )
                            layout = vis.get("layout") or "face"
                            if vis.get("has_onscreen_graphic"):
                                layout = "blur"
                            v_score = float(vis.get("visual_hook_score") or 5.0)
                            v_reason = (vis.get("visual_reason") or "").strip()

                            c["reframe"] = layout
                            c["visual_reason"] = v_reason
                            c["visual_score"] = v_score
                            if v_reason:
                                c["reason"] = f"[🎬 Vision: {layout.upper()} - {v_reason}] " + c["reason"]
                            else:
                                c["reason"] = f"[🎬 Vision: {layout.upper()}] " + c["reason"]

                            if v_score >= 8.0:
                                c["score"] = round(c["score"] + min(0.6, (v_score - 7.0) * 0.15), 2)
                        except Exception as vis_err:
                            log.warning("Vision analysis failed for clip %s: %s", idx, vis_err)
        except Exception as e:
            log.warning("Visual Director stage skipped or failed: %s", e)

    return top_clips
