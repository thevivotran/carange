from calendar import monthrange
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.database import Category, Transaction, TransactionType, get_db
from app.routers.fragments._helpers import render_fragment
from app.services import ollama as _ollama
from app.services.budget_service import compute_budget_rows

router = APIRouter()


@router.get("/digest")
async def fragment_pulse_digest(request: Request, db: Session = Depends(get_db)):
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

    this_total = sum(r.total for r in this_week_cats)
    top_cats = this_week_cats[:5]

    digest_text = None
    if _ollama.is_enabled() and this_total > 0:
        cat_lines = "\n".join(f"- {r.name}: {r.total:,.0f} VND" for r in top_cats)
        delta_pct = ((this_total - prev_total) / prev_total * 100) if prev_total else None
        delta_str = (
            f"So với tuần trước: {'tăng' if delta_pct >= 0 else 'giảm'} {abs(delta_pct):.0f}%"
            if delta_pct is not None
            else "Tuần trước chưa có dữ liệu"
        )
        prompt = (
            f"Tổng chi tiêu 7 ngày qua: {this_total:,.0f} VND\n"
            f"{delta_str}\n\n"
            f"Chi tiêu theo danh mục:\n{cat_lines}\n\n"
            "Viết 2-3 câu nhận xét ngắn gọn, thân thiện về chi tiêu tuần này. "
            "Nêu 1 điểm tích cực và 1 điểm cần chú ý nếu có. Không dùng emoji."
        )
        digest_text = await _ollama.generate(
            prompt=prompt,
            system=(
                "Bạn là trợ lý tài chính gia đình, viết bằng tiếng Việt, ngắn gọn và thực tế. Không dùng markdown."
            ),
        )

    return render_fragment(
        request,
        "partials/pulse/_digest.html",
        {
            "digest_text": digest_text,
            "this_total": this_total,
            "ollama_enabled": _ollama.is_enabled(),
        },
    )


@router.get("/budget-advisor")
async def fragment_pulse_budget_advisor(request: Request, db: Session = Depends(get_db)):
    today = date.today()
    year_month = f"{today.year:04d}-{today.month:02d}"
    _, days_in_month = monthrange(today.year, today.month)
    day_pct = round(today.day / days_in_month * 100)

    rows = compute_budget_rows(db, year_month)

    advisor_text = None
    if _ollama.is_enabled() and rows:
        over = [r for r in rows if r["usage_pct"] > 100]
        at_risk = [r for r in rows if 80 <= r["usage_pct"] <= 100]
        on_track = [r for r in rows if r["usage_pct"] < 80]

        def fmt(r):
            spent = r["this_month_spent"]
            alloc = r["monthly_allocation"]
            pct = r["usage_pct"]
            return f"- {r['category_name']}: đã chi {pct:.0f}% ({spent:,.0f} / {alloc:,.0f} VND)"

        over_lines = "\n".join(fmt(r) for r in over) or "Không có"
        risk_lines = "\n".join(fmt(r) for r in at_risk) or "Không có"
        ok_lines = "\n".join(fmt(r) for r in on_track[:3]) or "Không có"

        prompt = (
            f"Hôm nay là ngày {today.day}/{today.month}, tháng đã đi được {day_pct}%.\n\n"
            f"Danh mục vượt ngân sách:\n{over_lines}\n\n"
            f"Danh mục sắp vượt (80-100%):\n{risk_lines}\n\n"
            f"Danh mục ổn định (dưới 80%):\n{ok_lines}\n\n"
            "Viết 2-3 câu nhận xét ngắn gọn về tình hình ngân sách tháng này. "
            "Nêu rõ danh mục cần chú ý và 1 lời khuyên cụ thể nếu có. Không dùng emoji."
        )
        advisor_text = await _ollama.generate(
            prompt=prompt,
            system=(
                "Bạn là trợ lý tài chính gia đình, viết bằng tiếng Việt, ngắn gọn và thực tế. Không dùng markdown."
            ),
        )

    return render_fragment(
        request,
        "partials/pulse/_budget_advisor.html",
        {
            "advisor_text": advisor_text,
            "has_budget": bool(rows),
            "ollama_enabled": _ollama.is_enabled(),
            "over_count": len([r for r in rows if r["usage_pct"] > 100]),
            "at_risk_count": len([r for r in rows if 80 <= r["usage_pct"] <= 100]),
        },
    )
