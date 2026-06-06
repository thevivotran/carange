"""Persistent store for LLM-generated regex patterns, keyed by sender domain."""

import json
import logging
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

log = logging.getLogger("email_worker.learned_patterns")

_STORE_PATH = Path(__file__).parent / "learned_patterns.json"
_lock = threading.Lock()

_EMAIL_RE = re.compile(r"@([\w.\-]+)")


def _extract_domain(sender: str) -> str:
    m = _EMAIL_RE.search(sender)
    return m.group(1).lower() if m else ""


def _load() -> dict:
    try:
        with open(_STORE_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save(store: dict) -> None:
    with open(_STORE_PATH, "w") as f:
        json.dump(store, f, indent=2, ensure_ascii=False, default=str)


def get_patterns(sender: str) -> Optional[dict]:
    domain = _extract_domain(sender)
    if not domain:
        return None
    with _lock:
        store = _load()
    return store.get(domain)


def save_patterns(sender: str, patterns: dict) -> None:
    domain = _extract_domain(sender)
    if not domain:
        return
    with _lock:
        store = _load()
        existing = store.get(domain, {})
        patterns["generated_at"] = datetime.now().isoformat()
        patterns["success_count"] = existing.get("success_count", 0) + 1
        store[domain] = patterns
        _save(store)
    log.info("Saved learned patterns for domain: %s", domain)


def increment_hit(sender: str) -> None:
    """Track how many times a learned pattern successfully matched."""
    domain = _extract_domain(sender)
    if not domain:
        return
    with _lock:
        store = _load()
        if domain in store:
            store[domain]["success_count"] = store[domain].get("success_count", 0) + 1
            _save(store)
