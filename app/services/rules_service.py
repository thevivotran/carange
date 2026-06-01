"""Rules engine — normalize merchant names and apply transaction rules."""

import json
import logging
import re
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.models.database import Payee, Transaction, TransactionRule

log = logging.getLogger("app.rules_service")

_VALID_FIELDS = {"description", "amount", "payment_method", "source", "payee_id", "type"}
_VALID_OPS = {"equals", "contains", "regex", "range", "in", "gt", "lt"}


def normalize_description(db: Session, raw: str) -> tuple[str, Optional[int]]:
    """Match raw description against payee alias_patterns.

    Returns (canonical_name, payee_id) when a payee matches,
    or (raw, None) when nothing matches.
    """
    if not raw:
        return raw, None

    payees = db.query(Payee).filter(Payee.alias_patterns.isnot(None)).all()
    for payee in payees:
        try:
            patterns: list[str] = json.loads(payee.alias_patterns)
        except (json.JSONDecodeError, TypeError):
            continue
        for pattern in patterns:
            try:
                if re.search(pattern, raw, re.IGNORECASE):
                    return payee.canonical_name, payee.id
            except re.error:
                log.warning("Bad regex in payee %d: %r", payee.id, pattern)
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
            try:
                raw_action = json.loads(rule.action_json or "{}")
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
    else:
        return False

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

    return False
