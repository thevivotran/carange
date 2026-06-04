"""
vLLM LLM client — thin wrapper around vLLM's OpenAI-compatible API.

Configuration (env vars):
  OLLAMA_URL    Base URL of the vLLM server, e.g. http://ollama.ollama.svc.cluster.local:8000
                Leave empty (default) to disable all LLM features gracefully.
  OLLAMA_MODEL  Model name as served by vLLM (default: Qwen3.6-35B-A3B)

Design:
  - When OLLAMA_URL is unset, every call returns None immediately — no errors.
  - Provides both sync (generate_sync) and async (generate) variants so the
    module is usable from the OCR worker process (no event loop) and FastAPI routes.
  - All failures are caught and logged; callers always get None on error.
  - Uses /v1/chat/completions (OpenAI-compatible); think mode enabled via
    extra_body so Qwen3.6's chain-of-thought reasoning is always active.
"""

import base64
import logging
import os
from typing import Optional

import httpx

log = logging.getLogger("carange.ollama")

OLLAMA_URL = os.getenv("OLLAMA_URL", "").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "Qwen3.6-35B-A3B")

# 600s budget: 35B MoE + think mode with CPU offloading can take 3–8 min cold.
# Warm requests typically complete in 30–90s.
_GENERATE_TIMEOUT = 600.0
_HEALTH_TIMEOUT = 5.0

_CHAT_PATH = "/v1/chat/completions"
_HEALTH_PATH = "/health"


def is_enabled() -> bool:
    return bool(OLLAMA_URL)


def _build_messages(prompt: str, system: str) -> list[dict]:
    msgs = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.append({"role": "user", "content": prompt})
    return msgs


import re as _re

_THINK_RE = _re.compile(r"<think>.*?</think>", _re.DOTALL | _re.IGNORECASE)


def _extract_response(data: dict) -> str:
    content = data["choices"][0]["message"]["content"].strip()
    # Qwen3 think mode may embed <think>…</think> reasoning in content — strip it.
    content = _THINK_RE.sub("", content).strip()
    return content


# ── Sync client (OCR worker) ──────────────────────────────────────────────────


def check_health_sync() -> bool:
    if not is_enabled():
        return False
    try:
        r = httpx.get(f"{OLLAMA_URL}{_HEALTH_PATH}", timeout=_HEALTH_TIMEOUT)
        return r.status_code == 200
    except Exception as exc:
        log.debug("vLLM health check failed: %s", exc)
        return False


def generate_sync(
    prompt: str,
    system: str = "",
    model: str = OLLAMA_MODEL,
    temperature: float = 0.1,
) -> Optional[str]:
    """Blocking generate call. Returns None when vLLM is disabled or unreachable."""
    if not is_enabled():
        return None
    payload = {
        "model": model,
        "messages": _build_messages(prompt, system),
        "stream": False,
        "temperature": temperature,
        "extra_body": {"chat_template_kwargs": {"enable_thinking": True}},
    }
    try:
        r = httpx.post(
            f"{OLLAMA_URL}{_CHAT_PATH}",
            json=payload,
            timeout=httpx.Timeout(_GENERATE_TIMEOUT, connect=10.0),
        )
        r.raise_for_status()
        return _extract_response(r.json())
    except Exception as exc:
        log.warning("vLLM generate_sync failed: %s", exc)
        return None


def vision_sync(
    image_path: str,
    prompt: str,
    system: str = "",
    model: str = OLLAMA_MODEL,
) -> Optional[str]:
    """Blocking vision call — encodes image as base64 and sends to vLLM."""
    if not is_enabled():
        return None
    try:
        with open(image_path, "rb") as f:
            image_b64 = base64.b64encode(f.read()).decode()
    except OSError as exc:
        log.warning("vLLM vision: could not read image %s: %s", image_path, exc)
        return None

    content: list[dict] = [
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
        {"type": "text", "text": prompt},
    ]
    msgs = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.append({"role": "user", "content": content})

    payload = {
        "model": model,
        "messages": msgs,
        "stream": False,
        "temperature": 0.1,
        "extra_body": {"chat_template_kwargs": {"enable_thinking": True}},
    }
    try:
        r = httpx.post(
            f"{OLLAMA_URL}{_CHAT_PATH}",
            json=payload,
            timeout=httpx.Timeout(_GENERATE_TIMEOUT, connect=10.0),
        )
        r.raise_for_status()
        return _extract_response(r.json())
    except Exception as exc:
        log.warning("vLLM vision_sync failed: %s", exc)
        return None


# ── Async client (FastAPI routes) ─────────────────────────────────────────────


async def check_health() -> bool:
    if not is_enabled():
        return False
    try:
        async with httpx.AsyncClient(timeout=_HEALTH_TIMEOUT) as client:
            r = await client.get(f"{OLLAMA_URL}{_HEALTH_PATH}")
            return r.status_code == 200
    except Exception as exc:
        log.debug("vLLM async health check failed: %s", exc)
        return False


async def generate(
    prompt: str,
    system: str = "",
    model: str = OLLAMA_MODEL,
    temperature: float = 0.1,
) -> Optional[str]:
    """Async generate call. Returns None when vLLM is disabled or unreachable."""
    if not is_enabled():
        return None
    payload = {
        "model": model,
        "messages": _build_messages(prompt, system),
        "stream": False,
        "temperature": temperature,
        "extra_body": {"chat_template_kwargs": {"enable_thinking": True}},
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(_GENERATE_TIMEOUT, connect=10.0)) as client:
            r = await client.post(f"{OLLAMA_URL}{_CHAT_PATH}", json=payload)
            r.raise_for_status()
            return _extract_response(r.json())
    except Exception as exc:
        log.warning("vLLM generate failed: %s", exc)
        return None
