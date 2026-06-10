"""DB-backed store for LLM-generated regex patterns, keyed by sender domain.

Patterns live in the shared application database (table ``learned_patterns``)
so they survive pod restarts and are covered by the regular DB backups.

Lifecycle: ``failure_count`` counts consecutive misses and is reset by every
successful match; once it crosses MAX_CONSECUTIVE_FAILURES the row is dropped
so the LLM fallback re-learns the sender's (changed) format from scratch.
"""

import json
import logging
import re
from datetime import datetime, timezone
from typing import Optional

from app.models.database import LearnedPattern, SessionLocal

log = logging.getLogger("email_worker.learned_patterns")

MAX_CONSECUTIVE_FAILURES = 5

_EMAIL_RE = re.compile(r"@([\w.\-]+)")


def _extract_domain(sender: str) -> str:
    m = _EMAIL_RE.search(sender)
    return m.group(1).lower() if m else ""


def get_patterns(sender: str) -> Optional[dict]:
    domain = _extract_domain(sender)
    if not domain:
        return None
    with SessionLocal() as db:
        row = db.query(LearnedPattern).filter(LearnedPattern.domain == domain).first()
        if row is None:
            return None
        try:
            return json.loads(row.patterns)
        except json.JSONDecodeError:
            log.warning("Corrupt learned patterns for domain %s — dropping", domain)
            db.delete(row)
            db.commit()
            return None


def save_patterns(sender: str, patterns: dict) -> None:
    domain = _extract_domain(sender)
    if not domain:
        return
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        row = db.query(LearnedPattern).filter(LearnedPattern.domain == domain).first()
        if row is None:
            row = LearnedPattern(domain=domain, success_count=0, failure_count=0)
            db.add(row)
        row.patterns = json.dumps(patterns, ensure_ascii=False, default=str)
        row.generated_at = now
        row.failure_count = 0
        db.commit()
    log.info("Saved learned patterns for domain: %s", domain)


def record_success(sender: str) -> None:
    """A learned pattern matched — bump reliability, reset the failure streak."""
    domain = _extract_domain(sender)
    if not domain:
        return
    with SessionLocal() as db:
        row = db.query(LearnedPattern).filter(LearnedPattern.domain == domain).first()
        if row is None:
            return
        row.success_count = (row.success_count or 0) + 1
        row.failure_count = 0
        db.commit()


def record_failure(sender: str) -> None:
    """A learned pattern matched nothing — count the miss, drop the pattern
    after MAX_CONSECUTIVE_FAILURES so the LLM fallback re-learns the format."""
    domain = _extract_domain(sender)
    if not domain:
        return
    with SessionLocal() as db:
        row = db.query(LearnedPattern).filter(LearnedPattern.domain == domain).first()
        if row is None:
            return
        row.failure_count = (row.failure_count or 0) + 1
        if row.failure_count >= MAX_CONSECUTIVE_FAILURES:
            log.warning(
                "Dropping learned patterns for %s after %d consecutive misses — will re-learn",
                domain,
                row.failure_count,
            )
            db.delete(row)
        db.commit()
