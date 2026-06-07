from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from typing import Optional

from app.models.database import get_db
from app.routers.fragments._helpers import render_fragment
from app.services.dashboard_layout import get_visible_sections
from app.services.dashboard_service import get_dashboard_data
from app.services.settings_service import set_setting

router = APIRouter()


@router.post("/onboarding/dismiss")
def dismiss_onboarding(db: Session = Depends(get_db)):
    set_setting(db, "onboarding_complete", "true")
    return HTMLResponse(
        "",
        headers={
            "HX-Trigger": (
                '{"showToast": {"message": "Welcome! Start by adding your first transaction.", "type": "success"}}'
            )
        },
    )


@router.get("/safety-score")
def fragment_safety_score(
    request: Request,
    year: Optional[int] = None,
    month: Optional[int] = None,
    db: Session = Depends(get_db),
):
    data = get_dashboard_data(db, year=year, month=month)
    return render_fragment(
        request,
        "partials/dashboard/_safety_score.html",
        {
            "summary": data["summary"],
            "check_income": data["check_income"],
            "check_bds": data["check_bds"],
            "check_tk": data["check_tk"],
            "check_net": data["check_net"],
            "ss_score": data["ss_score"],
        },
    )


@router.get("/kpi-cards")
def fragment_kpi_cards(
    request: Request,
    year: Optional[int] = None,
    month: Optional[int] = None,
    db: Session = Depends(get_db),
):
    data = get_dashboard_data(db, year=year, month=month)
    s = data["summary"]
    return render_fragment(
        request,
        "partials/dashboard/_kpi_cards.html",
        {
            "summary": s,
            "visible_sections": get_visible_sections(db),
            "liquid_delta": s["liquid_savings_rate"] - s["prev_liquid_savings_rate"],
            "bds_delta": s["bds_rate"] - s["prev_bds_rate"],
            "net_delta": s["net_this_month"] - s["prev_net_cash"],
            "living_delta": s["living_expense_ratio"] - s["prev_living_expense_ratio"],
        },
    )
