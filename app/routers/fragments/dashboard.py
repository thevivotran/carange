from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session
from typing import Optional

from app.models.database import get_db, SavingsBundle, SavingsStatus
from app.routers.fragments._helpers import render_fragment
from app.services.dashboard_service import get_dashboard_data
from app.services.settings_service import get_setting, set_setting

router = APIRouter()


@router.get("/safety-score")
def fragment_safety_score(
    request: Request,
    year: Optional[int] = None,
    month: Optional[int] = None,
    db: Session = Depends(get_db),
):
    data = get_dashboard_data(db, year=year, month=month)
    s = data["summary"]
    check_income = s["total_income"] > 0
    check_bds = s["monthly_bds"] > 0
    check_tk = s["liquid_savings_rate"] >= s["savings_target_pct"]
    check_net = s["net_this_month"] > 0
    ss_score = sum([check_income, check_bds, check_tk, check_net])
    return render_fragment(
        request,
        "partials/dashboard/_safety_score.html",
        {
            "summary": s,
            "check_income": check_income,
            "check_bds": check_bds,
            "check_tk": check_tk,
            "check_net": check_net,
            "ss_score": ss_score,
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
            "liquid_delta": s["liquid_savings_rate"] - s["prev_liquid_savings_rate"],
            "bds_delta": s["bds_rate"] - s["prev_bds_rate"],
            "net_delta": s["net_this_month"] - s["prev_net_cash"],
            "living_delta": s["living_expense_ratio"] - s["prev_living_expense_ratio"],
        },
    )


@router.get("/settings-form")
def fragment_settings_form(
    request: Request,
    db: Session = Depends(get_db),
):
    savings_bundles = (
        db.query(SavingsBundle.id, SavingsBundle.name)
        .filter(SavingsBundle.status == SavingsStatus.ACTIVE, SavingsBundle.deleted_at.is_(None))
        .order_by(SavingsBundle.name)
        .all()
    )
    return render_fragment(
        request,
        "partials/dashboard/_settings_form.html",
        {
            "savings_target_pct": get_setting(db, "savings_target_pct", "25"),
            "fi_target_vnd": get_setting(db, "fi_target_vnd", ""),
            "baby_fund_bundle_id": get_setting(db, "baby_fund_bundle_id", ""),
            "savings_bundles": [{"id": r.id, "name": r.name} for r in savings_bundles],
        },
    )


@router.post("/settings")
async def fragment_save_settings(
    request: Request,
    db: Session = Depends(get_db),
):
    form = await request.form()
    allowed = {"savings_target_pct", "fi_target_vnd", "baby_fund_bundle_id"}
    for key in allowed:
        if key in form:
            set_setting(db, key, str(form[key]).strip())
    return render_fragment(
        request,
        "partials/dashboard/_settings_saved.html",
        {},
        toast="Settings saved",
        trigger_events={"dashboard-month-changed": True},
    )
