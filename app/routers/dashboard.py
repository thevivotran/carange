from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func, extract, case, and_, false as sqla_false
from datetime import date
from calendar import monthrange
from typing import Optional

from app.models.database import (
    get_db,
    Transaction,
    Category,
    TransactionType,
)
from app.models.schemas import DashboardSummary
from app.services.dashboard_service import get_dashboard_data

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
    today = date.today()
    months = []
    for i in range(11, -1, -1):
        total_months = today.month - 1 - i
        year_num = today.year + total_months // 12
        month_num = total_months % 12 + 1
        months.append((year_num, month_num))

    start_date = date(months[0][0], months[0][1], 1)

    rows = (
        db.query(
            extract("year", Transaction.date).label("year"),
            extract("month", Transaction.date).label("month"),
            func.sum(
                case(
                    (
                        and_(
                            Transaction.type == TransactionType.INCOME,
                            Transaction.is_savings_related == False,
                        ),
                        Transaction.amount,
                    ),
                    else_=0,
                )
            ).label("income"),
            func.sum(
                case(
                    (
                        and_(Transaction.type == TransactionType.EXPENSE, Transaction.is_savings_related == False),
                        Transaction.amount,
                    ),
                    else_=0,
                )
            ).label("expense"),
            func.sum(
                case(
                    (
                        and_(Transaction.type == TransactionType.EXPENSE, Transaction.is_savings_related == True),
                        Transaction.amount,
                    ),
                    else_=0,
                )
            ).label("savings"),
        )
        .filter(Transaction.date >= start_date, Transaction.deleted_at.is_(None))
        .group_by(
            extract("year", Transaction.date),
            extract("month", Transaction.date),
        )
        .all()
    )

    data_map = {(int(r.year), int(r.month)): r for r in rows}

    results = []
    for year_num, month_num in months:
        r = data_map.get((year_num, month_num))
        income = float(r.income) if r else 0
        expense = float(r.expense) if r else 0
        results.append(
            {
                "month": date(year_num, month_num, 1).strftime("%b %Y"),
                "income": income,
                "expense": expense,
                "savings": float(r.savings) if r else 0,
                "net": income - expense,
                "savings_rate": round((income - expense) / income * 100, 1) if income > 0 else 0,
            }
        )

    return results


@router.get("/dashboard/expense-by-category")
def get_expense_by_category(year: Optional[int] = None, month: Optional[int] = None, db: Session = Depends(get_db)):
    today = date.today()
    current_month = month or today.month
    current_year = year or today.year
    month_start = date(current_year, current_month, 1)
    _, last_day = monthrange(current_year, current_month)
    month_end = date(current_year, current_month, last_day)

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
    today = date.today()
    months = []
    for i in range(5, -1, -1):
        total_months = today.month - 1 - i
        year_num = today.year + total_months // 12
        month_num = total_months % 12 + 1
        months.append((year_num, month_num))

    start_date = date(months[0][0], months[0][1], 1)

    income_rows = (
        db.query(
            extract("year", Transaction.date).label("year"),
            extract("month", Transaction.date).label("month"),
            func.sum(case((Transaction.type == TransactionType.INCOME, Transaction.amount), else_=0)).label("income"),
        )
        .filter(Transaction.date >= start_date, Transaction.deleted_at.is_(None))
        .group_by(extract("year", Transaction.date), extract("month", Transaction.date))
        .all()
    )
    income_map = {(int(r.year), int(r.month)): float(r.income or 0) for r in income_rows}

    # Use is_wealth_building flag instead of hardcoded names
    tiet_kiem_ids = [
        r[0]
        for r in db.query(Category.id)
        .filter(Category.name == "Tiết kiệm", Category.type == TransactionType.EXPENSE)
        .all()
    ]
    bds_ids = [
        r[0]
        for r in db.query(Category.id)
        .filter(Category.name == "Bất động sản", Category.type == TransactionType.EXPENSE)
        .all()
    ]
    tk_filter = Transaction.category_id.in_(tiet_kiem_ids) if tiet_kiem_ids else sqla_false()
    bds_filter_wbt = Transaction.category_id.in_(bds_ids) if bds_ids else sqla_false()

    rows = (
        db.query(
            extract("year", Transaction.date).label("year"),
            extract("month", Transaction.date).label("month"),
            func.sum(case((tk_filter, Transaction.amount), else_=0)).label("tiet_kiem"),
            func.sum(case((bds_filter_wbt, Transaction.amount), else_=0)).label("bds"),
        )
        .filter(
            Transaction.date >= start_date,
            Transaction.type == TransactionType.EXPENSE,
            Transaction.deleted_at.is_(None),
        )
        .group_by(
            extract("year", Transaction.date),
            extract("month", Transaction.date),
        )
        .all()
    )

    data_map = {(int(r.year), int(r.month)): r for r in rows}

    results = []
    for year_num, month_num in months:
        r = data_map.get((year_num, month_num))
        tk = float(r.tiet_kiem) if r else 0
        bds = float(r.bds) if r else 0
        inc = income_map.get((year_num, month_num), 0)
        total = tk + bds
        results.append(
            {
                "month": date(year_num, month_num, 1).strftime("%b %Y"),
                "tiet_kiem": tk,
                "bds": bds,
                "total": total,
                "income": inc,
                "savings_rate": round(total / inc * 100, 1) if inc > 0 else 0,
            }
        )

    return results
