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

_SYSTEM_WEEKLY = (
    "Bạn là chuyên gia phân tích tài chính cá nhân với 15 năm kinh nghiệm tư vấn cho các gia đình Việt Nam. "
    "Nhiệm vụ: phân tích chi tiêu tuần và đưa ra nhận xét CHÍNH XÁC, NGẮN GỌN, có số liệu cụ thể. "
    "Viết bằng tiếng Việt. Không dùng markdown, không dùng emoji. "
    "Luôn trích dẫn con số cụ thể khi nhận xét."
)

_SYSTEM_BUDGET = (
    "Bạn là cố vấn tài chính cá nhân theo dõi ngân sách theo thời gian thực cho gia đình Việt Nam. "
    "Nhiệm vụ: đánh giá nhanh tác động của giao dịch vừa thêm và tình trạng ngân sách tháng hiện tại. "
    "Viết bằng tiếng Việt. Không dùng markdown, không dùng emoji. "
    "Lời khuyên phải CỤ THỂ, ĐO LƯỜNG ĐƯỢC — không nói chung chung."
)


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
            trend_3w = f"tăng liên tiếp 3 tuần ({nums})"
        elif this_expense < prev_expense < two_weeks_ago_expense:
            trend_3w = f"giảm liên tiếp 3 tuần ({nums})"
        else:
            trend_3w = f"không đồng đều ({nums})"
    elif prev_expense:
        trend_3w = f"tuần -2 chưa đủ dữ liệu; tuần trước {prev_expense:,.0f} VND"
    else:
        trend_3w = "chưa đủ dữ liệu lịch sử"

    if week_delta_pct is not None:
        direction = "tăng" if week_delta_pct >= 0 else "giảm"
        delta_str = f"{direction} {abs(week_delta_pct):.0f}% so tuần trước ({prev_expense:,.0f} VND)"
    else:
        delta_str = "tuần trước chưa có dữ liệu"

    cat_lines = "\n".join(
        f"  {i + 1}. {r.name}: {r.total:,.0f} VND ({r.total / this_expense * 100:.0f}%)"
        for i, r in enumerate(this_week_cats[:6])
    )

    income_line = f"{this_income:,.0f} VND" if this_income > 0 else "không có ghi nhận"
    ratio_line = f" (chi = {expense_income_ratio}% thu nhập tuần)" if expense_income_ratio else ""
    largest_line = f"{largest.amount:,.0f} VND ({largest.name})" if largest else "không xác định"

    return f"""[PHÂN TÍCH CHI TIÊU TUẦN — {week_start.strftime("%d/%m")} đến {today.strftime("%d/%m/%Y")}]

Thu nhập tuần: {income_line}
Chi tiêu tuần: {this_expense:,.0f} VND{ratio_line}
Chi tiêu/ngày TB: {daily_avg:,.0f} VND (tuần trước: {prev_daily_avg:,.0f} VND)
Số giao dịch: {tx_count}
Giao dịch lớn nhất: {largest_line}
So tuần trước: {delta_str}
Xu hướng 3 tuần: {trend_3w}

Top danh mục chi tiêu:
{cat_lines}

---
Viết ĐÚNG 3 dòng ngắn, mỗi dòng bắt đầu bằng tiêu đề in hoa:
NHẬN XÉT: [1 câu tóm tắt tình hình tổng thể, trích dẫn ít nhất 1 con số]
ĐÁNG CHÚ Ý: [1 điểm nổi bật — tích cực hoặc đáng lo, có số liệu cụ thể]
KHUYẾN NGHỊ: [1 hành động cụ thể, đo lường được cho 7 ngày tới]"""


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
        text = _ollama.generate_sync(prompt=prompt, system=_SYSTEM_WEEKLY)
        if text:
            _upsert(db, InsightType.WEEKLY_DIGEST, text)
    except Exception:
        log.exception("Failed to generate weekly digest")
    finally:
        db.close()


# ── Budget Advisor ────────────────────────────────────────────────────────────


def _build_budget_advisor_prompt(
    db: Session,
    trigger_transaction_id: Optional[int] = None,
) -> Optional[str]:
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

    # ── Triggering transaction ────────────────────────────────────────────────
    trigger_tx = None
    if trigger_transaction_id:
        trigger_tx = db.query(Transaction).filter(Transaction.id == trigger_transaction_id).first()

    # ── Budget status ─────────────────────────────────────────────────────────
    over = [r for r in rows if r["usage_pct"] > 100]
    at_risk = [r for r in rows if 80 <= r["usage_pct"] <= 100]
    on_track = [r for r in rows if r["usage_pct"] < 80]

    def fmt_budget(r: dict) -> str:
        balance = r.get("available_balance", 0)
        balance_str = f"còn {balance:,.0f}" if balance >= 0 else f"vượt {abs(balance):,.0f}"
        return (
            f"  - {r['category_name']}: {r['usage_pct']:.0f}% ngân sách"
            f" ({r['this_month_spent']:,.0f}/{r['monthly_allocation']:,.0f} VND, {balance_str} VND)"
        )

    over_lines = "\n".join(fmt_budget(r) for r in over) or "  Không có"
    risk_lines = "\n".join(fmt_budget(r) for r in at_risk) or "  Không có"
    ok_sample = "\n".join(fmt_budget(r) for r in on_track[:3]) or "  Không có"

    # Budget utilization vs time utilization gap
    total_allocated = sum(r["monthly_allocation"] for r in rows)
    total_spent = sum(r["this_month_spent"] for r in rows)
    budget_pct = round(total_spent / total_allocated * 100) if total_allocated > 0 else 0
    budget_vs_time_gap = budget_pct - day_pct

    if budget_vs_time_gap > 15:
        pace_note = (
            f"CHI TIÊU NHANH HƠN KẾ HOẠCH: đã dùng {budget_pct}%"
            f" ngân sách nhưng chỉ qua {day_pct}% tháng (+{budget_vs_time_gap}%)"
        )
    elif budget_vs_time_gap < -15:
        pace_note = (
            f"CHI TIÊU CHẬM HƠN KẾ HOẠCH: mới dùng {budget_pct}% ngân sách sau {day_pct}% tháng ({budget_vs_time_gap}%)"
        )
    else:
        pace_note = f"tốc độ chi tiêu phù hợp: {budget_pct}% ngân sách / {day_pct}% tháng"

    # ── Triggering transaction context ────────────────────────────────────────
    if trigger_tx:
        cat_name = db.query(Category.name).filter(Category.id == trigger_tx.category_id).scalar() or "Không rõ danh mục"
        tx_type_str = "Chi tiêu" if trigger_tx.type == TransactionType.EXPENSE else "Thu nhập"
        trigger_block = (
            f"\n[GIAO DỊCH VỪA THÊM]\n"
            f"  {tx_type_str}: {trigger_tx.amount:,.0f} VND\n"
            f"  Danh mục: {cat_name}\n"
            f"  Mô tả: {trigger_tx.description or '(không có)'}\n"
            f"  Ngày: {trigger_tx.date.strftime('%d/%m/%Y')}"
        )
    else:
        trigger_block = ""

    income_line = f"{mtd_income:,.0f} VND" if mtd_income > 0 else "chưa ghi nhận"
    ratio_line = f" ({expense_income_ratio}% thu nhập)" if expense_income_ratio else ""

    return f"""[ĐÁNH GIÁ NGÂN SÁCH THỜI GIAN THỰC — Ngày {today.day}/{today.month}/{today.year}]

Tiến độ tháng: {days_elapsed}/{days_in_month} ngày ({day_pct}%), còn {days_remaining} ngày
Thu nhập tháng đến nay: {income_line}
Chi tiêu tháng đến nay: {mtd_expense:,.0f} VND{ratio_line}
Dự báo cuối tháng: {projected_expense:,.0f} VND (nếu giữ đà hiện tại)
Tốc độ ngân sách: {pace_note}
{trigger_block}
[TÌNH TRẠNG NGÂN SÁCH]
Vượt ngân sách ({len(over)} danh mục):
{over_lines}

Cảnh báo 80-100% ({len(at_risk)} danh mục):
{risk_lines}

Ổn định dưới 80% (mẫu):
{ok_sample}

---
Viết ĐÚNG 2-3 câu ngắn theo thứ tự:
1. Đánh giá nhanh giao dịch vừa thêm trong bối cảnh ngân sách\
{" (nếu không có giao dịch cụ thể, đánh giá tổng thể)" if not trigger_tx else ""}
2. Cảnh báo cụ thể nếu có rủi ro (hoặc xác nhận tích cực nếu tình hình tốt), trích dẫn số liệu
3. Một điều chỉnh CỤ THỂ, ĐO LƯỜNG ĐƯỢC có thể thực hiện ngay (nếu cần cải thiện)"""


def generate_budget_advisor_sync(trigger_transaction_id: Optional[int] = None) -> None:
    """Regenerate and store the budget advisor insight. Called after each new transaction."""
    if not _ollama.is_enabled():
        return
    db: Session = SessionLocal()
    try:
        prompt = _build_budget_advisor_prompt(db, trigger_transaction_id=trigger_transaction_id)
        if prompt is None:
            log.debug("Budget advisor: no budget rows, skipping")
            return
        text = _ollama.generate_sync(prompt=prompt, system=_SYSTEM_BUDGET)
        if text:
            _upsert(db, InsightType.BUDGET_ADVISOR, text, trigger_id=trigger_transaction_id)
    except Exception:
        log.exception("Failed to generate budget advisor")
    finally:
        db.close()
