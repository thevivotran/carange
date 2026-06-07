"""
AI insight generation service.

Schedules (enforced by the scheduler thread, not wall-clock):
  WEEKLY_DIGEST  — regenerated every 12 hours.
  BUDGET_ADVISOR — regenerated every 2 hours.

Both generators are fully synchronous (use generate_sync + SessionLocal) so they
work from daemon threads with no active event loop.

The pulse page reads pre-computed rows from ai_insights — zero LLM wait on load.
"""

import logging
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.database import (
    AIInsight,
    Category,
    InsightType,
    SessionLocal,
    Transaction,
    TransactionType,
)
from app.services import ollama as _ollama
from app.services.budget_service import compute_budget_rows

log = logging.getLogger("carange.insight")

_SYSTEM_WEEKLY = (
    "You are a personal finance analyst with 15 years of experience advising families. "
    "Task: analyze the week's spending and give ACCURATE, CONCISE commentary backed by specific numbers. "
    "Use English. No markdown, no emoji. "
    "DATA RULE: only cite figures exactly as provided — do not round, estimate, or compute new figures."
)

_SYSTEM_BUDGET = (
    "You are a personal finance advisor tracking a family's real-time monthly budget. "
    "Task: evaluate the current month's budget status and give practical, actionable advice. "
    "Use English. No markdown, no emoji. "
    "DATA RULE: only cite figures exactly as provided — do not round, estimate, or compute new figures. "
    "Advice must be SPECIFIC and MEASURABLE — avoid generic statements."
)


WEEKLY_DIGEST_MAX_AGE_HOURS = 12
BUDGET_ADVISOR_MAX_AGE_HOURS = 2


# ── Read / staleness ──────────────────────────────────────────────────────────


def get_insight(db: Session, insight_type: InsightType) -> Optional[AIInsight]:
    return db.query(AIInsight).filter(AIInsight.insight_type == insight_type).first()


def _is_stale(db: Session, insight_type: InsightType, max_age_hours: float) -> bool:
    """Return True if the insight doesn't exist or is older than max_age_hours."""
    row = get_insight(db, insight_type)
    if row is None:
        return True
    ts = row.generated_at
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    age_seconds = (datetime.now(timezone.utc) - ts).total_seconds()
    return age_seconds >= max_age_hours * 3600


# ── Write (upsert) ────────────────────────────────────────────────────────────


def _upsert(
    db: Session,
    insight_type: InsightType,
    content: str,
    trigger_id: Optional[int] = None,
) -> None:
    row = get_insight(db, insight_type)
    now = datetime.now(timezone.utc)
    if row:
        row.content = content
        row.generated_at = now
        row.trigger_transaction_id = trigger_id
    else:
        row = AIInsight(
            insight_type=insight_type,
            content=content,
            generated_at=now,
            trigger_transaction_id=trigger_id,
        )
        db.add(row)
    db.commit()
    log.info("Stored %s insight", insight_type.value)


# ── Weekly Digest ─────────────────────────────────────────────────────────────


def _build_weekly_digest_prompt(db: Session) -> Optional[str]:
    today = date.today()
    week_start = today - timedelta(days=7)
    prev_week_start = today - timedelta(days=14)
    two_weeks_ago_start = today - timedelta(days=21)

    # ── This week ──────────────────────────────────────────────────────────────
    this_week_cats = (
        db.query(Category.name, func.sum(Transaction.amount).label("total"))
        .join(Transaction, Transaction.category_id == Category.id)
        .filter(
            Transaction.type == TransactionType.EXPENSE,
            Transaction.date >= week_start,
            Transaction.date <= today,
            Transaction.deleted_at.is_(None),
        )
        .group_by(Category.name)
        .order_by(func.sum(Transaction.amount).desc())
        .all()
    )

    this_expense = sum(r.total for r in this_week_cats)
    if this_expense == 0:
        return None

    this_income = (
        db.query(func.sum(Transaction.amount))
        .filter(
            Transaction.type == TransactionType.INCOME,
            Transaction.date >= week_start,
            Transaction.date <= today,
            Transaction.deleted_at.is_(None),
        )
        .scalar()
        or 0
    )

    tx_count = (
        db.query(func.count(Transaction.id))
        .filter(
            Transaction.date >= week_start,
            Transaction.date <= today,
            Transaction.deleted_at.is_(None),
        )
        .scalar()
        or 0
    )

    # Largest single expense this week
    largest = (
        db.query(Transaction.amount, Category.name)
        .join(Category, Transaction.category_id == Category.id)
        .filter(
            Transaction.type == TransactionType.EXPENSE,
            Transaction.date >= week_start,
            Transaction.date <= today,
            Transaction.deleted_at.is_(None),
        )
        .order_by(Transaction.amount.desc())
        .first()
    )

    # ── Previous weeks for trend ───────────────────────────────────────────────
    prev_expense = (
        db.query(func.sum(Transaction.amount))
        .filter(
            Transaction.type == TransactionType.EXPENSE,
            Transaction.date >= prev_week_start,
            Transaction.date < week_start,
            Transaction.deleted_at.is_(None),
        )
        .scalar()
        or 0
    )

    two_weeks_ago_expense = (
        db.query(func.sum(Transaction.amount))
        .filter(
            Transaction.type == TransactionType.EXPENSE,
            Transaction.date >= two_weeks_ago_start,
            Transaction.date < prev_week_start,
            Transaction.deleted_at.is_(None),
        )
        .scalar()
        or 0
    )

    # ── Format ────────────────────────────────────────────────────────────────
    expense_income_ratio = round(this_expense / this_income * 100) if this_income > 0 else None
    daily_avg = round(this_expense / 7)
    prev_daily_avg = round(prev_expense / 7) if prev_expense else 0

    week_delta_pct = ((this_expense - prev_expense) / prev_expense * 100) if prev_expense else None

    # 3-week trend arrow
    if two_weeks_ago_expense and prev_expense and this_expense:
        nums = f"{two_weeks_ago_expense:,.0f} → {prev_expense:,.0f} → {this_expense:,.0f} VND"
        if this_expense > prev_expense > two_weeks_ago_expense:
            trend_3w = f"rising for 3 straight weeks ({nums})"
        elif this_expense < prev_expense < two_weeks_ago_expense:
            trend_3w = f"falling for 3 straight weeks ({nums})"
        else:
            trend_3w = f"mixed ({nums})"
    elif prev_expense:
        trend_3w = f"week -2 lacks data; last week {prev_expense:,.0f} VND"
    else:
        trend_3w = "not enough historical data"

    if week_delta_pct is not None:
        direction = "up" if week_delta_pct >= 0 else "down"
        delta_str = f"{direction} {abs(week_delta_pct):.0f}% vs last week ({prev_expense:,.0f} VND)"
    else:
        delta_str = "no data for last week"

    cat_lines = "\n".join(
        f"  {i + 1}. {r.name}: {r.total:,.0f} VND ({r.total / this_expense * 100:.0f}%)"
        for i, r in enumerate(this_week_cats[:6])
    )

    income_line = f"{this_income:,.0f} VND" if this_income > 0 else "no income recorded"
    ratio_line = f" (spending = {expense_income_ratio}% of weekly income)" if expense_income_ratio else ""
    largest_line = f"{largest.amount:,.0f} VND ({largest.name})" if largest else "unknown"

    return f"""[WEEKLY SPENDING ANALYSIS — {week_start.strftime("%d/%m")} to {today.strftime("%d/%m/%Y")}]

Weekly income: {income_line}
Weekly expense: {this_expense:,.0f} VND{ratio_line}
Avg daily spend: {daily_avg:,.0f} VND (last week: {prev_daily_avg:,.0f} VND)
Transaction count: {tx_count}
Largest expense: {largest_line}
Vs last week: {delta_str}
3-week trend: {trend_3w}

Top spending categories:
{cat_lines}

---
Write EXACTLY 3 short lines, each starting with an uppercase header on its own line:
SUMMARY: [1 sentence summarizing the overall situation, citing at least 1 figure]
NOTABLE: [1 highlight — positive or concerning — with a specific figure]
RECOMMENDATION: [1 specific, measurable action for the next 7 days]"""


def generate_weekly_digest_sync() -> None:
    """Regenerate weekly digest if older than WEEKLY_DIGEST_MAX_AGE_HOURS (12 h)."""
    if not _ollama.is_enabled():
        return
    db: Session = SessionLocal()
    try:
        if not _is_stale(db, InsightType.WEEKLY_DIGEST, WEEKLY_DIGEST_MAX_AGE_HOURS):
            log.debug("Weekly digest still fresh, skipping")
            return
        prompt = _build_weekly_digest_prompt(db)
        if prompt is None:
            log.debug("Weekly digest: no expenses in last 7 days, skipping")
            return
        log.info("Generating weekly digest...")
        text = _ollama.generate_sync(prompt=prompt, system=_SYSTEM_WEEKLY)
        if text:
            _upsert(db, InsightType.WEEKLY_DIGEST, text)
    except Exception:
        log.exception("Failed to generate weekly digest")
    finally:
        db.close()


# ── Budget Advisor ────────────────────────────────────────────────────────────


def _build_budget_advisor_prompt(db: Session) -> Optional[str]:
    today = date.today()
    year_month = f"{today.year:04d}-{today.month:02d}"
    _, days_in_month = monthrange(today.year, today.month)
    days_elapsed = today.day
    days_remaining = days_in_month - today.day
    day_pct = round(days_elapsed / days_in_month * 100)

    rows = compute_budget_rows(db, year_month)
    if not rows:
        return None

    # ── Month-to-date income & expense ────────────────────────────────────────
    month_start = date(today.year, today.month, 1)

    mtd_income = (
        db.query(func.sum(Transaction.amount))
        .filter(
            Transaction.type == TransactionType.INCOME,
            Transaction.date >= month_start,
            Transaction.date <= today,
            Transaction.deleted_at.is_(None),
        )
        .scalar()
        or 0
    )

    mtd_expense = (
        db.query(func.sum(Transaction.amount))
        .filter(
            Transaction.type == TransactionType.EXPENSE,
            Transaction.date >= month_start,
            Transaction.date <= today,
            Transaction.deleted_at.is_(None),
        )
        .scalar()
        or 0
    )

    # Projected month-end spend at current pace
    projected_expense = round(mtd_expense / days_elapsed * days_in_month) if days_elapsed > 0 else mtd_expense
    expense_income_ratio = round(mtd_expense / mtd_income * 100) if mtd_income > 0 else None

    # ── Budget status ─────────────────────────────────────────────────────────
    over = [r for r in rows if r["usage_pct"] > 100]
    at_risk = [r for r in rows if 80 <= r["usage_pct"] <= 100]
    on_track = [r for r in rows if r["usage_pct"] < 80]

    def fmt_budget(r: dict) -> str:
        balance = r.get("available_balance", 0)
        balance_str = f"{balance:,.0f} left" if balance >= 0 else f"{abs(balance):,.0f} over"
        return (
            f"  - {r['category_name']}: {r['usage_pct']:.0f}% of budget"
            f" ({r['this_month_spent']:,.0f}/{r['monthly_allocation']:,.0f} VND, {balance_str})"
        )

    over_lines = "\n".join(fmt_budget(r) for r in over) or "  None"
    risk_lines = "\n".join(fmt_budget(r) for r in at_risk) or "  None"
    ok_sample = "\n".join(fmt_budget(r) for r in on_track[:3]) or "  None"

    # Budget utilization vs time utilization gap
    total_allocated = sum(r["monthly_allocation"] for r in rows)
    total_spent = sum(r["this_month_spent"] for r in rows)
    budget_pct = round(total_spent / total_allocated * 100) if total_allocated > 0 else 0
    budget_vs_time_gap = budget_pct - day_pct

    if budget_vs_time_gap > 15:
        pace_note = (
            f"SPENDING FASTER THAN PLANNED: used {budget_pct}%"
            f" of budget but only {day_pct}% of the month has passed (+{budget_vs_time_gap}%)"
        )
    elif budget_vs_time_gap < -15:
        pace_note = (
            f"SPENDING SLOWER THAN PLANNED: only used {budget_pct}% of budget"
            f" after {day_pct}% of the month ({budget_vs_time_gap}%)"
        )
    else:
        pace_note = f"spending pace on track: {budget_pct}% of budget / {day_pct}% of month"

    income_line = f"{mtd_income:,.0f} VND" if mtd_income > 0 else "not recorded yet"
    ratio_line = f" ({expense_income_ratio}% of income)" if expense_income_ratio else ""

    return f"""[BUDGET REVIEW — {today.day}/{today.month}/{today.year}]

Month progress: {days_elapsed}/{days_in_month} days ({day_pct}%), {days_remaining} days left
Income month-to-date: {income_line}
Expense month-to-date: {mtd_expense:,.0f} VND{ratio_line}
End-of-month forecast: {projected_expense:,.0f} VND (at current pace)
Spending pace: {pace_note}

[BUDGET STATUS]
Over budget ({len(over)} categories):
{over_lines}

At risk 80-100% ({len(at_risk)} categories):
{risk_lines}

On track under 80% (sample):
{ok_sample}

---
Write EXACTLY 2-3 short sentences, in this order:
1. Overall assessment of this month's budget status, noting spend vs plan and the end-of-month forecast
2. A specific warning if there's risk (or a positive confirmation if things look good), citing exact figures
3. One SPECIFIC, MEASURABLE adjustment that can be made right now (if improvement is needed)"""


def generate_budget_advisor_sync() -> None:
    """Regenerate budget advisor if older than BUDGET_ADVISOR_MAX_AGE_HOURS (2 h)."""
    if not _ollama.is_enabled():
        return
    db: Session = SessionLocal()
    try:
        if not _is_stale(db, InsightType.BUDGET_ADVISOR, BUDGET_ADVISOR_MAX_AGE_HOURS):
            log.debug("Budget advisor still fresh, skipping")
            return
        prompt = _build_budget_advisor_prompt(db)
        if prompt is None:
            log.debug("Budget advisor: no budget rows, skipping")
            return
        log.info("Generating budget advisor...")
        text = _ollama.generate_sync(prompt=prompt, system=_SYSTEM_BUDGET)
        if text:
            _upsert(db, InsightType.BUDGET_ADVISOR, text)
    except Exception:
        log.exception("Failed to generate budget advisor")
    finally:
        db.close()
