"""Rules engine — apply transaction rules."""

import json
import logging
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.models.database import Category, Transaction, TransactionRule

log = logging.getLogger("app.rules_service")

_VALID_FIELDS = {"description", "amount", "payment_method", "source", "type"}
_VALID_OPS = {"equals", "contains", "regex", "range", "in", "gt", "lt"}


class RuleAction:
    def __init__(self) -> None:
        self.category_id: Optional[int] = None
        self.auto_approve: bool = False
        self.force_needs_review: bool = False


def load_active_rules(db: Session) -> list[TransactionRule]:
    """Fetch active rules ordered by priority, for reuse across a batch of calls to apply_rules()."""
    return (
        db.query(TransactionRule)
        .filter(TransactionRule.is_active == True)
        .order_by(TransactionRule.priority.asc(), TransactionRule.id.asc())
        .all()
    )


def apply_rules(
    db: Session,
    tx: Transaction,
    rules: Optional[list[TransactionRule]] = None,
) -> RuleAction:
    """Apply the first matching active rule (ordered by priority asc) to tx.

    Mutates rule stats (match_count, last_matched_at) but does NOT commit.
    Returns a RuleAction describing what should change on the transaction.

    Pass ``rules`` (e.g. from :func:`load_active_rules`) to skip the query when
    applying rules to many transactions in one batch.
    """
    action = RuleAction()
    if rules is None:
        rules = load_active_rules(db)

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


# ── Rule suggestions ──────────────────────────────────────────────────────────

SUGGEST_LOOKBACK_DAYS = 180
SUGGEST_MIN_PURITY = 0.9


def normalise_description(text: Optional[str]) -> str:
    """'GRAB*Ride 12/11 #4821' → 'grab*ride'. Digits vary per transaction (dates,
    order numbers), so they are dropped before grouping."""
    text = re.sub(r"\d+", " ", (text or "").lower())
    text = re.sub(r"(?<!\w)[#/:.,\-]+(?!\w)", " ", text)  # punctuation left behind by the digits
    return re.sub(r"\s+", " ", text).strip()


def _common_fragment(descriptions: list[str]) -> Optional[str]:
    """The longest digit-free piece of the first description that every description
    contains — what a "description contains" rule can match on. The normalised group
    key can't be used: dropping digits from the middle ("chuyen tien 123 cho") leaves
    text that isn't a substring of the originals."""
    lowered = [d.lower() for d in descriptions]
    pieces = {p.strip(" \t#/:.,-") for p in re.split(r"\d+", lowered[0])}
    for piece in sorted(pieces, key=lambda p: (-len(p), p)):
        if len(piece) >= 3 and all(piece in d for d in lowered):
            return piece
    return None


def suggest_rules(db: Session, min_count: int = 5, limit: int = 5, today: Optional[date] = None) -> list[dict]:
    """Descriptions the user keeps filing under the same category without a rule.

    A suggestion needs ≥ ``min_count`` transactions in the lookback window, at least
    SUGGEST_MIN_PURITY of them in one category, and no active rule already matching.
    """
    today = today or date.today()
    txs = (
        db.query(Transaction)
        .filter(
            Transaction.deleted_at.is_(None),
            Transaction.date >= today - timedelta(days=SUGGEST_LOOKBACK_DAYS),
            Transaction.description.isnot(None),
        )
        .all()
    )
    groups: dict[str, list[Transaction]] = defaultdict(list)
    for tx in txs:
        key = normalise_description(tx.description)
        if len(key) >= 3:
            groups[key].append(tx)

    rules = load_active_rules(db)
    out = []
    for members in groups.values():
        if len(members) < min_count:
            continue
        category_id, count = Counter(t.category_id for t in members).most_common(1)[0]
        if count / len(members) < SUGGEST_MIN_PURITY:
            continue
        fragment = _common_fragment([t.description for t in members])
        if fragment is None:
            continue
        if any(_matches(rule, t) for rule in rules for t in members):
            continue
        out.append({"pattern": fragment, "category_id": category_id, "count": count})

    out.sort(key=lambda s: (-s["count"], s["pattern"]))
    out = out[:limit]
    names = dict(db.query(Category.id, Category.name).filter(Category.id.in_([s["category_id"] for s in out])).all())
    for s in out:
        s["category_name"] = names.get(s["category_id"], "")
    return out
