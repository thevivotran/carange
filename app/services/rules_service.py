"""Rules engine — normalize merchant names and apply transaction rules."""

import json
import logging
import re
import threading
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.models.database import Payee, Transaction, TransactionRule

log = logging.getLogger("app.rules_service")

_VALID_FIELDS = {"description", "amount", "payment_method", "source", "payee_id", "type"}
_VALID_OPS = {"equals", "contains", "regex", "range", "in", "gt", "lt"}

# ── Payee pattern cache ───────────────────────────────────────────────────────
# Stores list of (payee_id, canonical_name, [compiled_regex, ...]) tuples.
_payee_cache: list[tuple[int, str, list[re.Pattern]]] | None = None
_payee_cache_lock = threading.Lock()


def invalidate_payee_cache() -> None:
    """Clear the compiled-pattern cache. Call after any payee write."""
    global _payee_cache
    with _payee_cache_lock:
        _payee_cache = None


def _load_payee_cache(db: Session) -> list[tuple[int, str, list[re.Pattern]]]:
    global _payee_cache
    with _payee_cache_lock:
        if _payee_cache is not None:
            return _payee_cache
        payees = db.query(Payee).filter(Payee.alias_patterns.isnot(None)).all()
        compiled = []
        for payee in payees:
            try:
                raw = payee.alias_patterns
                # JSON column returns a Python list directly; TEXT fallback needs parsing
                if isinstance(raw, str):
                    patterns: list[str] = json.loads(raw)
                elif isinstance(raw, list):
                    patterns = raw
                else:
                    continue
            except (json.JSONDecodeError, TypeError):
                continue
            rxs: list[re.Pattern] = []
            for p in patterns:
                try:
                    rxs.append(re.compile(p, re.IGNORECASE))
                except re.error:
                    log.warning("Bad regex in payee %d: %r", payee.id, p)
            if rxs:
                compiled.append((payee.id, payee.canonical_name, rxs))
        _payee_cache = compiled
        return _payee_cache


def normalize_description(db: Session, raw: str) -> tuple[str, Optional[int]]:
    """Match raw description against payee alias_patterns.

    Returns (raw, payee_id) when a payee matches — description is intentionally
    kept verbatim so detail information is not lost.
    Returns (raw, None) when nothing matches.
    Patterns are pre-compiled and cached; invalidated on payee writes.
    """
    if not raw:
        return raw, None

    for payee_id, canonical_name, rxs in _load_payee_cache(db):
        for rx in rxs:
            if rx.search(raw):
                return raw, payee_id
    return raw, None


class RuleAction:
    def __init__(self) -> None:
        self.category_id: Optional[int] = None
        self.auto_approve: bool = False
        self.force_needs_review: bool = False


def apply_rules(db: Session, tx: Transaction, payee_id: Optional[int] = None) -> RuleAction:
    """Apply the first matching active rule (ordered by priority asc) to tx.

    Mutates rule stats (match_count, last_matched_at) but does NOT commit.
    Returns a RuleAction describing what should change on the transaction.
    """
    action = RuleAction()
    rules = (
        db.query(TransactionRule)
        .filter(TransactionRule.is_active == True)
        .order_by(TransactionRule.priority.asc(), TransactionRule.id.asc())
        .all()
    )

    for rule in rules:
        if _matches(rule, tx, payee_id):
            raw = rule.action_json
            try:
                if isinstance(raw, str):
                    raw_action = json.loads(raw or "{}")
                elif isinstance(raw, dict):
                    raw_action = raw
                else:
                    raw_action = {}
            except json.JSONDecodeError:
                log.warning("Rule %d has invalid action_json — skipping", rule.id)
                continue

            if "set_category_id" in raw_action:
                action.category_id = int(raw_action["set_category_id"])
            if raw_action.get("auto_approve"):
                action.auto_approve = True
            if raw_action.get("force_needs_review"):
                action.force_needs_review = True

            rule.match_count = (rule.match_count or 0) + 1
            rule.last_matched_at = datetime.now(timezone.utc)
            log.debug("Rule %d '%s' matched tx description=%r", rule.id, rule.name, tx.description)
            break

    return action


def _matches(rule: TransactionRule, tx: Transaction, payee_id: Optional[int]) -> bool:
    field = rule.match_field
    op = rule.match_op
    pattern = rule.match_value or ""

    if field not in _VALID_FIELDS or op not in _VALID_OPS:
        return False

    if field == "description":
        val = tx.description or ""
    elif field == "amount":
        val = str(tx.amount or 0)
    elif field == "payment_method":
        val = tx.payment_method or ""
    elif field == "source":
        val = tx.source or ""
    elif field == "payee_id":
        val = str(payee_id) if payee_id is not None else ""
    elif field == "type":
        val = tx.type.value if tx.type else ""

    if op == "equals":
        return val.lower() == pattern.lower()
    elif op == "contains":
        return pattern.lower() in val.lower()
    elif op == "regex":
        try:
            return bool(re.search(pattern, val, re.IGNORECASE))
        except re.error:
            return False
    elif op == "range":
        try:
            lo, hi = pattern.split(",", 1)
            numeric = float(val)
            return float(lo) <= numeric <= float(hi)
        except (ValueError, TypeError):
            return False
    elif op == "in":
        allowed = {v.strip().lower() for v in pattern.split(",")}
        return val.lower() in allowed
    elif op == "gt":
        try:
            return float(val) > float(pattern)
        except (ValueError, TypeError):
            return False
    elif op == "lt":
        try:
            return float(val) < float(pattern)
        except (ValueError, TypeError):
            return False
