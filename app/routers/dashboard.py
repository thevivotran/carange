from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import date
from typing import Optional

from app.models.database import (
    get_db,
    Transaction,
    Category,
    TransactionType,
)
from app.models.schemas import DashboardSummary
from app.services.dashboard_service import get_dashboard_data, get_kpi_role_category_ids
from app.services.fiscal_period import (
    current_period_ym,
    fiscal_window_ym,
    get_month_start_day,
    shift_period_ym,
)

router = APIRouter()


def get_dashboard_page_data(db: Session, year: int = None, month: int = None) -> dict:
    return get_dashboard_data(db, year=year, month=month)


# ── API endpoints ─────────────────────────────────────────────────────────────


@router.get("/dashboard/summary", response_model=DashboardSummary)
def get_dashboard_summary(year: Optional[int] = None, month: Optional[int] = None, db: Session = Depends(get_db)):
    data = get_dashboard_page_data(db, year=year, month=month)
    s = data["summary"]
    return DashboardSummary(
        total_income_month=s["total_income"],
        total_expense_month=s["total_expense"],
        total_savings_month=s["total_savings_expense"],
        net_this_month=s["net_this_month"],
        savings_rate=s["savings_rate"],
        liquid_savings_rate=s["liquid_savings_rate"],
        bds_rate=s["bds_rate"],
        living_expense_ratio=s["living_expense_ratio"],
        emergency_fund_months=s["emergency_fund_months"],
        avg_monthly_expense=s["avg_monthly_expense"],
        prev_liquid_savings_rate=s["prev_liquid_savings_rate"],
        prev_bds_rate=s["prev_bds_rate"],
        prev_net_cash=s["prev_net_cash"],
        prev_living_expense_ratio=s["prev_living_expense_ratio"],
        stress_test_required=s["stress_test_required"],
        stress_test_cushion=s["stress_test_cushion"],
        net_worth=s["net_worth"],
        cash_on_hand=s["cash_on_hand"],
        total_savings_active=s["total_savings"],
        total_savings_target=s["total_savings_initial"],
        total_assets_current=s["total_assets_current"],
        total_assets_purchase=s["total_assets_purchase"],
        total_assets_count=s["total_assets_count"],
        total_projects_paid=s["total_projects_paid"],
        active_projects_count=s["active_projects"],
        completed_projects_count=s["completed_projects"],
        budget_adherence_pct=s["budget_adherence_pct"],
        monthly_tiet_kiem=s["monthly_tiet_kiem"],
        monthly_bds=s["monthly_bds"],
        savings_target_pct=s["savings_target_pct"],
        fi_target_vnd=s["fi_target_vnd"],
        fi_progress_pct=s["fi_progress_pct"],
        runway_months=s["runway_months"],
        net_worth_growth_rate=s["net_worth_growth_rate"],
        passive_income_monthly=s["passive_income_monthly"],
        passive_income_pct=s["passive_income_pct"],
    )


@router.get("/dashboard/monthly-trend")
def get_monthly_trend(db: Session = Depends(get_db)):
    day = get_month_start_day(db)
    today = date.today()
    cur_year, cur_month = current_period_ym(today, day)
    periods = [shift_period_ym(cur_year, cur_month, -i) for i in range(11, -1, -1)]

    overall_start, _ = fiscal_window_ym(periods[0][0], periods[0][1], day)
    _, overall_end = fiscal_window_ym(periods[-1][0], periods[-1][1], day)

    rows = (
        db.query(
            Transaction.date,
            Transaction.amount,
            Transaction.type,
            Transaction.is_savings_related,
        )
        .filter(
            Transaction.date >= overall_start,
            Transaction.date <= overall_end,
            Transaction.deleted_at.is_(None),
        )
        .all()
    )

    results = []
    for year_num, month_num in periods:
        start, end = fiscal_window_ym(year_num, month_num, day)
        income = 0.0
        expense = 0.0
        savings = 0.0
        for r in rows:
            if not (start <= r.date <= end):
                continue
            amt = float(r.amount)
            if r.type == TransactionType.INCOME and not r.is_savings_related:
                income += amt
            elif r.type == TransactionType.EXPENSE and not r.is_savings_related:
                expense += amt
            elif r.type == TransactionType.EXPENSE and r.is_savings_related:
                savings += amt
        results.append(
            {
                "month": date(year_num, month_num, 1).strftime("%b %Y"),
                "income": income,
                "expense": expense,
                "savings": savings,
                "net": income - expense,
                "savings_rate": round((income - expense) / income * 100, 1) if income > 0 else 0,
            }
        )

    return results


@router.get("/dashboard/expense-by-category")
def get_expense_by_category(year: Optional[int] = None, month: Optional[int] = None, db: Session = Depends(get_db)):
    today = date.today()
    day = get_month_start_day(db)
    if year is not None and month is not None:
        current_year, current_month = year, month
    else:
        current_year, current_month = current_period_ym(today, day)
    month_start, month_end = fiscal_window_ym(current_year, current_month, day)

    total_expense = (
        db.query(func.sum(Transaction.amount))
        .filter(
            Transaction.date >= month_start,
            Transaction.date <= month_end,
            Transaction.type == TransactionType.EXPENSE,
            Transaction.is_savings_related == False,
            Transaction.deleted_at.is_(None),
        )
        .scalar()
        or 1
    )

    cat_rows = (
        db.query(
            Category.name,
            Category.color,
            func.sum(Transaction.amount),
        )
        .join(Transaction)
        .filter(
            Transaction.date >= month_start,
            Transaction.date <= month_end,
            Transaction.type == TransactionType.EXPENSE,
            Transaction.is_savings_related == False,
            Transaction.deleted_at.is_(None),
        )
        .group_by(Category.id)
        .all()
    )

    return [
        {
            "category_name": name,
            "category_color": color,
            "total": total,
            "percentage": round(total / total_expense * 100, 1),
        }
        for name, color, total in cat_rows
        if total > 0
    ]


@router.get("/dashboard/wealth-building-trend")
def get_wealth_building_trend(db: Session = Depends(get_db)):
    day = get_month_start_day(db)
    today = date.today()
    cur_year, cur_month = current_period_ym(today, day)
    periods = [shift_period_ym(cur_year, cur_month, -i) for i in range(5, -1, -1)]

    overall_start, _ = fiscal_window_ym(periods[0][0], periods[0][1], day)
    _, overall_end = fiscal_window_ym(periods[-1][0], periods[-1][1], day)

    rows = (
        db.query(
            Transaction.date,
            Transaction.amount,
            Transaction.type,
            Transaction.category_id,
        )
        .filter(
            Transaction.date >= overall_start,
            Transaction.date <= overall_end,
            Transaction.deleted_at.is_(None),
        )
        .all()
    )

    kpi_ids = get_kpi_role_category_ids(db)
    tk_set = set(kpi_ids["liquid_savings"])
    bds_set = set(kpi_ids["real_estate"])

    results = []
    for year_num, month_num in periods:
        start, end = fiscal_window_ym(year_num, month_num, day)
        income = 0.0
        tiet_kiem = 0.0
        bds = 0.0
        for r in rows:
            if not (start <= r.date <= end):
                continue
            amt = float(r.amount)
            if r.type == TransactionType.INCOME:
                income += amt
            elif r.type == TransactionType.EXPENSE:
                if r.category_id in tk_set:
                    tiet_kiem += amt
                if r.category_id in bds_set:
                    bds += amt
        total = tiet_kiem + bds
        results.append(
            {
                "month": date(year_num, month_num, 1).strftime("%b %Y"),
                "tiet_kiem": tiet_kiem,
                "bds": bds,
                "total": total,
                "income": income,
                "savings_rate": round(total / income * 100, 1) if income > 0 else 0,
            }
        )

    return results
