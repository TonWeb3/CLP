"""LLM client. Works with Ollama (default) or any OpenAI-compatible API."""
from __future__ import annotations

import json
import re
import time

import requests


class LLMError(RuntimeError):
    pass


def parse_json(text: str):
    text = (text or "").strip()
    # Strip <think>...</think> reasoning blocks from modern thinking models
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.I).strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    a, b = text.find("{"), text.rfind("}")
    if a != -1 and b > a:
        try:
            return json.loads(text[a:b + 1])
        except json.JSONDecodeError:
            pass
    raise LLMError("The model did not return valid JSON.")


def _post(url: str, payload: dict, headers: dict | None = None, timeout: int = 900, tries: int = 3):
    last = None
    for attempt in range(tries):
        try:
            r = requests.post(url, json=payload, headers=headers or {}, timeout=timeout)
        except requests.ConnectionError as e:
            raise LLMError(f"Cannot reach the LLM server at {url.split('/api')[0].split('/chat')[0]}. "
                           "Is Ollama running?") from e
        except requests.Timeout as e:
            raise LLMError("The LLM took too long to answer.") from e
        if r.status_code in (429, 500, 502, 503) and attempt < tries - 1:
            time.sleep(2 * (attempt + 1))
            last = r
            continue
        return r
    return last


def chat_json(cfg: dict, model: str, system: str, user: str, schema: dict, images: list[str] | None = None):
    """Ask for a JSON answer. Returns the parsed object.
    Supports optional base64-encoded images for multimodal Vision models.
    """
    if not model:
        raise LLMError("No LLM model selected. Open the Models page and choose one.")
    provider = cfg.get("llm_provider", "ollama")
    temperature = float(cfg.get("llm_temperature", 0.2))

    if provider == "ollama":
        host = cfg["ollama_host"]
        user_msg: dict = {"role": "user", "content": user}
        if images:
            user_msg["images"] = images
        payload = {
            "model": model,
            "stream": False,
            "messages": [{"role": "system", "content": system}, user_msg],
            "format": schema,
            "options": {"temperature": temperature, "num_ctx": int(cfg.get("llm_num_ctx", 16384))},
            "keep_alive": "10m",
        }
        if cfg.get("llm_disable_thinking", True):
            payload["think"] = False
        r = _post(host + "/api/chat", payload)
        if r.status_code == 400 and "think" in payload and "think" in r.text.lower():
            payload.pop("think")
            r = _post(host + "/api/chat", payload)
        if r.status_code == 404:
            raise LLMError(f"Model '{model}' is not installed in Ollama. Pull it on the Models page.")
        if r.status_code != 200:
            raise LLMError(f"Ollama error {r.status_code}: {r.text[:300]}")
        try:
            content = (r.json().get("message") or {}).get("content", "")
        except Exception as e:
            raise LLMError("Ollama returned an invalid response.") from e
        return parse_json(content)

    # OpenAI-compatible (Groq, OpenRouter, Gemini's compatibility endpoint, LM Studio, vLLM ...)
    base = cfg.get("openai_base_url", "").rstrip("/")
    if not base:
        raise LLMError("Set the API base URL in Settings.")
    headers = {"Authorization": f"Bearer {cfg.get('llm_api_key', '')}"}
    sys_full = system + "\n\nReturn ONLY a JSON object matching this JSON schema:\n" + json.dumps(schema)

    if images:
        user_parts: list[dict] = [{"type": "text", "text": user}]
        for img_b64 in images:
            user_parts.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}})
        user_msg = {"role": "user", "content": user_parts}
    else:
        user_msg = {"role": "user", "content": user}

    payload = {
        "model": model,
        "temperature": temperature,
        "messages": [{"role": "system", "content": sys_full}, user_msg],
        "response_format": {"type": "json_object"},
    }
    r = _post(base + "/chat/completions", payload, headers)
    if r.status_code == 400 and "response_format" in r.text:
        payload.pop("response_format")
        r = _post(base + "/chat/completions", payload, headers)
    if r.status_code in (401, 403):
        raise LLMError("The API key was rejected. Check it in Settings.")
    if r.status_code != 200:
        raise LLMError(f"LLM API error {r.status_code}: {r.text[:300]}")
    try:
        content = r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        raise LLMError("The LLM API returned an invalid response structure.") from e
    return parse_json(content)


def test_connection(cfg: dict) -> dict:
    provider = cfg.get("llm_provider", "ollama")
    try:
        if provider == "ollama":
            r = requests.get(cfg["ollama_host"] + "/api/tags", timeout=6)
            r.raise_for_status()
            names = [m["name"] for m in r.json().get("models", [])]
            return {"ok": True, "message": f"Connected to Ollama. {len(names)} model(s) installed.", "models": names}
        base = cfg.get("openai_base_url", "").rstrip("/")
        if not base:
            return {"ok": False, "message": "Set the API base URL first."}
        r = requests.get(base + "/models", headers={"Authorization": f"Bearer {cfg.get('llm_api_key', '')}"}, timeout=10)
        if r.status_code in (401, 403):
            return {"ok": False, "message": "The API key was rejected."}
        r.raise_for_status()
        data = r.json().get("data", [])
        names = [m.get("id", "") for m in data][:200]
        return {"ok": True, "message": f"Connected. {len(names)} model(s) listed.", "models": names}
    except requests.ConnectionError:
        return {"ok": False, "message": "Cannot reach the server. Check the address and that it is running."}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": str(e)[:300]}


SOCIAL_SCHEMA = {
    "type": "object",
    "properties": {
        "tiktok": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "caption": {"type": "string"},
                "hashtags": {"type": "array", "items": {"type": "string"}},
                "first_comment": {"type": "string"},
            },
            "required": ["title", "caption", "hashtags", "first_comment"],
        },
        "youtube": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "description": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "pinned_comment": {"type": "string"},
            },
            "required": ["title", "description", "tags", "pinned_comment"],
        },
        "virality_score": {
            "type": "object",
            "properties": {
                "overall": {"type": "number"},
                "hook_rating": {"type": "string"},
                "key_takeaway": {"type": "string"},
            },
            "required": ["overall", "hook_rating", "key_takeaway"],
        },
    },
    "required": ["tiktok", "youtube", "virality_score"],
}


def generate_social_post(cfg: dict, clip_title: str, hook: str, transcript_snippet: str) -> dict:
    model = cfg.get("ranker_model") or cfg.get("filter_model")
    if not model:
        raise LLMError("No model selected in Settings. Choose a model first.")

    system = (
        "You are a world-class viral short-form social media strategist specializing in TikTok and YouTube Shorts algorithms.\n"
        "Generate platform-specific, high-CTR, high-engagement copy for the provided video clip.\n\n"
        "Guidelines:\n"
        "1. TikTok: punchy curiosity title (<60 chars), engaging 1-2 sentence caption that invites discussion, 5-8 trending niche hashtags, and a polarizing or conversational first comment.\n"
        "2. YouTube Shorts: click-worthy title (<90 chars with #Shorts), informative SEO description with keywords, relevant tags, and a pinned comment prompt.\n"
        "3. Virality Score: score 1-10 with hook evaluation and takeaway."
    )
    user = (
        f"Clip Title: {clip_title}\n"
        f"Hook / Opening Line: {hook}\n"
        f"Transcript:\n{transcript_snippet}\n"
    )
    return chat_json(cfg, model, system, user, SOCIAL_SCHEMA)


VISION_DIRECTOR_SCHEMA = {
    "type": "object",
    "properties": {
        "layout": {
            "type": "string",
            "enum": ["face", "split", "gaming", "blur", "center"],
        },
        "is_gaming": {
            "type": "boolean",
        },
        "webcam_quadrant": {
            "type": "string",
            "enum": ["bottom_right", "bottom_left", "top_right", "top_left", "center", "none"],
        },
        "visual_hook_score": {
            "type": "number",
        },
        "visual_reason": {
            "type": "string",
        },
        "has_onscreen_graphic": {
            "type": "boolean",
        },
        "primary_speaker_side": {
            "type": "string",
            "enum": ["left", "right", "center", "both"],
        },
        "tracking_style": {
            "type": "string",
            "enum": ["active_speaker", "smooth_follow", "lock_primary", "static_center"],
        },
        "ignore_background_faces": {
            "type": "boolean",
        },
        "scenes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "scene_index": {"type": "integer"},
                    "layout": {
                        "type": "string",
                        "enum": ["face", "split", "gaming", "blur", "center"],
                    },
                    "focus_side": {
                        "type": "string",
                        "enum": ["left", "right", "center", "both", "auto"],
                    },
                    "has_graphic": {"type": "boolean"},
                },
                "required": ["scene_index", "layout"],
            },
        },
    },
    "required": ["layout", "visual_reason", "has_onscreen_graphic", "scenes"],
}


def analyze_visual_framing(
    cfg: dict,
    model: str,
    images_b64: list[str],
    title: str = "",
    transcript_snippet: str = "",
    scene_descriptions: list[str] | None = None,
) -> dict:
    """Master Vision Director: scans scenes across the clip and issues scene-by-scene camera & face-tracking directives to OpenCV YuNet and FFmpeg."""
    system = (
        "You are the Master AI Video Director and Cinematographer for short-form vertical video (TikTok, YouTube Shorts, Reels).\n"
        "You are given chronological keyframe images (or storyboard contact sheets) representing numbered scenes across a clip.\n"
        "You are the MASTER controller: your directives command the OpenCV YuNet neural face tracker and the FFmpeg multi-layout compositor.\n\n"
        "Available framing layouts (can be set overall AND switched per scene in `scenes`):\n"
        "1. 'face': Follow the active speaker talking to the camera (ideal for podcasts, interviews, sermons, comedy, keynotes, vlogs, education). If there are background faces, crowd members, posters, or reaction images on a screen behind the speaker, set `ignore_background_faces: true` so OpenCV never jumps to a bystander or TV screen.\n"
        "2. 'blur': MUST BE USED for any scene where an ON-SCREEN GRAPHIC, image, tweet, chart, presentation slide, screen share, code editor, scoreboard, or wide diagram appears! This switches the video to a full-frame letterboxed view over a blurred background so the graphic/image is 100% readable and never cropped in half.\n"
        "3. 'split': Use when TWO distinct people are visible in the same wide shot having a back-and-forth conversation (e.g. podcast host on left, guest on right). Stacks both speakers top & bottom.\n"
        "4. 'gaming': Use when VIDEO GAME gameplay / HUD OR a Reaction/Commentary video is on screen with a creator facecam/webcam overlay in a corner. Stacks the creator facecam on top (36%) and gameplay/reacted content on bottom (64%).\n"
        "5. 'center': Use for full-height cinematic B-roll, sports/fitness action, cooking/product close-ups, or wide group shots where no single face should be cropped in and no text graphic is being cut off.\n\n"
        "Your Master Director Responsibilities:\n"
        "- Inspect EVERY numbered scene image in order.\n"
        "- If a speaker is talking in Scene 1 ('face'), then shows an image/chart/screen-share in Scene 2 ('blur'), and returns to talking in Scene 3 ('face'), output those exact layouts in `scenes` so the renderer dynamically switches layouts mid-clip without looking like a bad edit!\n"
        "- For each scene in `scenes`, specify `scene_index` (1-based), `layout` ('face', 'blur', 'split', 'gaming', 'center'), `focus_side` ('left', 'right', 'center', 'both', 'auto'), and `has_graphic` (true if an image/slide/text graphic is shown in that scene).\n"
        "- Set `tracking_style`: 'active_speaker' (cuts to whoever's mouth is moving), 'smooth_follow' (glides with a single moving speaker), 'lock_primary' (locks onto the main speaker and ignores bystanders), or 'static_center'.\n"
        "- Set `webcam_quadrant` if a streamer facecam is in a corner ('bottom_right', 'bottom_left', 'top_right', 'top_left', 'none').\n"
        "- Set `visual_reason`: concise summary of your directing plan across the clip.\n"
        "Return valid JSON matching the schema."
    )
    scene_list_txt = "\n".join(scene_descriptions) if scene_descriptions else f"{len(images_b64)} chronological keyframes across the clip."
    user = (
        f"Clip Title: {title}\n"
        f"Spoken Transcript: {transcript_snippet}\n"
        f"Chronological Scenes to Direct:\n{scene_list_txt}\n\n"
        "Inspect each scene and return the Master Vision Director JSON plan."
    )
    return chat_json(cfg, model, system, user, VISION_DIRECTOR_SCHEMA, images=images_b64)
