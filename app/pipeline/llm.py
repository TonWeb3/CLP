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


def chat_json(cfg: dict, model: str, system: str, user: str, schema: dict):
    """Ask for a JSON answer. Returns the parsed object."""
    if not model:
        raise LLMError("No LLM model selected. Open the Models page and choose one.")
    provider = cfg.get("llm_provider", "ollama")
    temperature = float(cfg.get("llm_temperature", 0.2))

    if provider == "ollama":
        host = cfg["ollama_host"]
        payload = {
            "model": model,
            "stream": False,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
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
        content = (r.json().get("message") or {}).get("content", "")
        return parse_json(content)

    # OpenAI-compatible (Groq, OpenRouter, Gemini's compatibility endpoint, LM Studio, vLLM ...)
    base = cfg.get("openai_base_url", "").rstrip("/")
    if not base:
        raise LLMError("Set the API base URL in Settings.")
    headers = {"Authorization": f"Bearer {cfg.get('llm_api_key', '')}"}
    sys_full = system + "\n\nReturn ONLY a JSON object matching this JSON schema:\n" + json.dumps(schema)
    payload = {
        "model": model,
        "temperature": temperature,
        "messages": [{"role": "system", "content": sys_full}, {"role": "user", "content": user}],
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
    content = r.json()["choices"][0]["message"]["content"]
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
