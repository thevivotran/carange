"""
AI insight generation service.

Triggers:
  WEEKLY_DIGEST  — generated every hour by the background scheduler thread.
  BUDGET_ADVISOR — generated after every new transaction via FastAPI BackgroundTask.

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

_SYSTEM = "Bạn là trợ lý tài chính gia đình, viết bằng tiếng Việt, ngắn gọn và thực tế. Không dùng markdown."


# ── Read ──────────────────────────────────────────────────────────────────────


def get_insight(db: Session, insight_type: InsightType) -> Optional[AIInsight]:
    return db.query(AIInsight).filter(AIInsight.insight_type == insight_type).first()


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

    this_total = sum(r.total for r in this_week_cats)
    if this_total == 0:
        return None

    prev_total = (
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

    cat_lines = "\n".join(f"- {r.name}: {r.total:,.0f} VND" for r in this_week_cats[:5])
    delta_pct = ((this_total - prev_total) / prev_total * 100) if prev_total else None
    delta_str = (
        f"So với tuần trước: {'tăng' if delta_pct >= 0 else 'giảm'} {abs(delta_pct):.0f}%"
        if delta_pct is not None
        else "Tuần trước chưa có dữ liệu"
    )
    return (
        f"Tổng chi tiêu 7 ngày qua: {this_total:,.0f} VND\n"
        f"{delta_str}\n\n"
        f"Chi tiêu theo danh mục:\n{cat_lines}\n\n"
        "Viết 2-3 câu nhận xét ngắn gọn, thân thiện về chi tiêu tuần này. "
        "Nêu 1 điểm tích cực và 1 điểm cần chú ý nếu có. Không dùng emoji."
    )


def generate_weekly_digest_sync() -> None:
    """Regenerate and store the weekly digest. Called every hour by the scheduler thread."""
    if not _ollama.is_enabled():
        return
    db: Session = SessionLocal()
    try:
        prompt = _build_weekly_digest_prompt(db)
        if prompt is None:
            log.debug("Weekly digest: no expenses in last 7 days, skipping")
            return
        text = _ollama.generate_sync(prompt=prompt, system=_SYSTEM)
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
    day_pct = round(today.day / days_in_month * 100)

    rows = compute_budget_rows(db, year_month)
    if not rows:
        return None

    over = [r for r in rows if r["usage_pct"] > 100]
    at_risk = [r for r in rows if 80 <= r["usage_pct"] <= 100]
    on_track = [r for r in rows if r["usage_pct"] < 80]

    def fmt(r: dict) -> str:
        return (
            f"- {r['category_name']}: đã chi {r['usage_pct']:.0f}%"
            f" ({r['this_month_spent']:,.0f} / {r['monthly_allocation']:,.0f} VND)"
        )

    return (
        f"Hôm nay là ngày {today.day}/{today.month}, tháng đã đi được {day_pct}%.\n\n"
        f"Danh mục vượt ngân sách:\n{chr(10).join(fmt(r) for r in over) or 'Không có'}\n\n"
        f"Danh mục sắp vượt (80-100%):\n{chr(10).join(fmt(r) for r in at_risk) or 'Không có'}\n\n"
        f"Danh mục ổn định (dưới 80%):\n{chr(10).join(fmt(r) for r in on_track[:3]) or 'Không có'}\n\n"
        "Viết 2-3 câu nhận xét ngắn gọn về tình hình ngân sách tháng này. "
        "Nêu rõ danh mục cần chú ý và 1 lời khuyên cụ thể nếu có. Không dùng emoji."
    )


def generate_budget_advisor_sync(trigger_transaction_id: Optional[int] = None) -> None:
    """Regenerate and store the budget advisor insight. Called after each new transaction."""
    if not _ollama.is_enabled():
        return
    db: Session = SessionLocal()
    try:
        prompt = _build_budget_advisor_prompt(db)
        if prompt is None:
            log.debug("Budget advisor: no budget rows, skipping")
            return
        text = _ollama.generate_sync(prompt=prompt, system=_SYSTEM)
        if text:
            _upsert(db, InsightType.BUDGET_ADVISOR, text, trigger_id=trigger_transaction_id)
    except Exception:
        log.exception("Failed to generate budget advisor")
    finally:
        db.close()
