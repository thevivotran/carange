"""
Ollama LLM client — thin wrapper around the Ollama HTTP API.

Configuration (env vars):
  OLLAMA_URL    Base URL of the Ollama server, e.g. http://thevix:11434
                Leave empty (default) to disable all LLM features gracefully.
  OLLAMA_MODEL  Model tag to use (default: qwen3.5:9b)

Design:
  - When OLLAMA_URL is unset, every call returns None immediately — no errors.
  - Provides both sync (generate_sync) and async (generate) variants so the
    module is usable from the OCR worker process (no event loop) and FastAPI routes.
  - All failures are caught and logged; callers always get None on error.
"""

import base64
import logging
import os
from typing import Optional

import httpx

log = logging.getLogger("carange.ollama")

OLLAMA_URL = os.getenv("OLLAMA_URL", "").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3.5:9b")

# 120s covers cold-start model load (~30s) + inference (~20-40s) in one budget.
# Warm requests typically complete in under 10s.
_GENERATE_TIMEOUT = 120.0
_HEALTH_TIMEOUT = 5.0


def is_enabled() -> bool:
    return bool(OLLAMA_URL)


# ── Sync client (OCR worker) ──────────────────────────────────────────────────


def check_health_sync() -> bool:
    if not is_enabled():
        return False
    try:
        r = httpx.get(f"{OLLAMA_URL}/api/tags", timeout=_HEALTH_TIMEOUT)
        return r.status_code == 200
    except Exception as exc:
        log.debug("Ollama health check failed: %s", exc)
        return False


def generate_sync(
    prompt: str,
    system: str = "",
    model: str = OLLAMA_MODEL,
    temperature: float = 0.1,
) -> Optional[str]:
    """Blocking generate call. Returns None when Ollama is disabled or unreachable."""
    if not is_enabled():
        return None
    payload: dict = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": temperature},
        "think": False,
    }
    if system:
        payload["system"] = system
    try:
        r = httpx.post(
            f"{OLLAMA_URL}/api/generate",
            json=payload,
            timeout=httpx.Timeout(_GENERATE_TIMEOUT, connect=10.0),
        )
        r.raise_for_status()
        return r.json()["response"].strip()
    except Exception as exc:
        log.warning("Ollama generate_sync failed: %s", exc)
        return None


def vision_sync(
    image_path: str,
    prompt: str,
    system: str = "",
    model: str = OLLAMA_MODEL,
) -> Optional[str]:
    """Blocking vision call — encodes image as base64 and sends to Ollama."""
    if not is_enabled():
        return None
    try:
        with open(image_path, "rb") as f:
            image_b64 = base64.b64encode(f.read()).decode()
    except OSError as exc:
        log.warning("Ollama vision: could not read image %s: %s", image_path, exc)
        return None

    payload: dict = {
        "model": model,
        "prompt": prompt,
        "images": [image_b64],
        "stream": False,
        "options": {"temperature": 0.1},
        "think": False,
    }
    if system:
        payload["system"] = system
    try:
        r = httpx.post(
            f"{OLLAMA_URL}/api/generate",
            json=payload,
            timeout=httpx.Timeout(_GENERATE_TIMEOUT, connect=10.0),
        )
        r.raise_for_status()
        return r.json()["response"].strip()
    except Exception as exc:
        log.warning("Ollama vision_sync failed: %s", exc)
        return None


# ── Async client (FastAPI routes) ─────────────────────────────────────────────


async def check_health() -> bool:
    if not is_enabled():
        return False
    try:
        async with httpx.AsyncClient(timeout=_HEALTH_TIMEOUT) as client:
            r = await client.get(f"{OLLAMA_URL}/api/tags")
            return r.status_code == 200
    except Exception as exc:
        log.debug("Ollama async health check failed: %s", exc)
        return False


async def generate(
    prompt: str,
    system: str = "",
    model: str = OLLAMA_MODEL,
    temperature: float = 0.1,
) -> Optional[str]:
    """Async generate call. Returns None when Ollama is disabled or unreachable."""
    if not is_enabled():
        return None
    payload: dict = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": temperature},
        "think": False,
    }
    if system:
        payload["system"] = system
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(_GENERATE_TIMEOUT, connect=10.0)) as client:
            r = await client.post(f"{OLLAMA_URL}/api/generate", json=payload)
            r.raise_for_status()
            return r.json()["response"].strip()
    except Exception as exc:
        log.warning("Ollama generate failed: %s", exc)
        return None
