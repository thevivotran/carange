"""Rules engine — apply transaction rules."""

import json
import logging
import re
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.models.database import Transaction, TransactionRule

log = logging.getLogger("app.rules_service")

_VALID_FIELDS = {"description", "amount", "payment_method", "source", "type"}
_VALID_OPS = {"equals", "contains", "regex", "range", "in", "gt", "lt"}


class RuleAction:
    def __init__(self) -> None:
        self.category_id: Optional[int] = None
        self.auto_approve: bool = False
        self.force_needs_review: bool = False


def apply_rules(db: Session, tx: Transaction) -> RuleAction:
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
        if _matches(rule, tx):
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


def _matches(rule: TransactionRule, tx: Transaction) -> bool:
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
