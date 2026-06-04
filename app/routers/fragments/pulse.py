from calendar import monthrange
from datetime import date

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.models.database import InsightType, get_db
from app.routers.fragments._helpers import render_fragment
from app.services import ollama as _ollama
from app.services.budget_service import compute_budget_rows
from app.services.insight_service import get_insight

router = APIRouter()


@router.get("/digest")
async def fragment_pulse_digest(request: Request, db: Session = Depends(get_db)):
    insight = get_insight(db, InsightType.WEEKLY_DIGEST)
    return render_fragment(
        request,
        "partials/pulse/_digest.html",
        {
            "digest_text": insight.content if insight else None,
            "generated_at": insight.generated_at if insight else None,
            "ollama_enabled": _ollama.is_enabled(),
        },
    )


@router.get("/budget-advisor")
async def fragment_pulse_budget_advisor(request: Request, db: Session = Depends(get_db)):
    today = date.today()
    year_month = f"{today.year:04d}-{today.month:02d}"
    _, days_in_month = monthrange(today.year, today.month)

    rows = compute_budget_rows(db, year_month)
    insight = get_insight(db, InsightType.BUDGET_ADVISOR)

    return render_fragment(
        request,
        "partials/pulse/_budget_advisor.html",
        {
            "advisor_text": insight.content if insight else None,
            "generated_at": insight.generated_at if insight else None,
            "has_budget": bool(rows),
            "ollama_enabled": _ollama.is_enabled(),
            "over_count": len([r for r in rows if r["usage_pct"] > 100]),
            "at_risk_count": len([r for r in rows if 80 <= r["usage_pct"] <= 100]),
        },
    )
