from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func, extract, case, and_
from datetime import date, timedelta
from typing import List, Optional

from app.models.database import (
    get_db, Transaction, Category, SavingsBundle, FinancialProject,
    SavingsStatus, ProjectStatus, TransactionType, OtherAsset,
    ProjectPayment, PaymentStatus,
)
from app.models.schemas import DashboardSummary, MonthlyData, CategorySummary

router = APIRouter()


def get_dashboard_page_data(db: Session, year: int = None, month: int = None) -> dict:
    today = date.today()
    current_month = month or today.month
    current_year  = year  or today.year

    # ── Monthly figures (affected by month selector) ───────────────────────
    monthly_income = db.query(func.sum(Transaction.amount)).filter(
        extract('month', Transaction.date) == current_month,
        extract('year',  Transaction.date) == current_year,
        Transaction.type == TransactionType.INCOME,
    ).scalar() or 0

    monthly_expense = db.query(func.sum(Transaction.amount)).filter(
        extract('month', Transaction.date) == current_month,
        extract('year',  Transaction.date) == current_year,
        Transaction.type == TransactionType.EXPENSE,
        Transaction.is_savings_related == False,
    ).scalar() or 0

    monthly_savings = db.query(func.sum(Transaction.amount)).filter(
        extract('month', Transaction.date) == current_month,
        extract('year',  Transaction.date) == current_year,
        Transaction.type == TransactionType.EXPENSE,
        Transaction.is_savings_related == True,
    ).scalar() or 0

    savings_rate = round(
        (monthly_income - monthly_expense) / monthly_income * 100, 1
    ) if monthly_income > 0 else 0

    # ── Static / current-state figures ────────────────────────────────────
    savings_data = db.query(
        func.sum(SavingsBundle.future_amount),
        func.sum(SavingsBundle.initial_deposit),
    ).filter(SavingsBundle.status == SavingsStatus.ACTIVE).first()
    total_savings         = (savings_data[0] or 0) if savings_data else 0
    total_savings_initial = (savings_data[1] or 0) if savings_data else 0

    active_projects_count = db.query(FinancialProject).filter(
        FinancialProject.status.in_([ProjectStatus.PLANNING, ProjectStatus.IN_PROGRESS])
    ).count()
    completed_projects_count = db.query(FinancialProject).filter(
        FinancialProject.status == ProjectStatus.COMPLETED
    ).count()

    total_income_all  = db.query(func.sum(Transaction.amount)).filter(Transaction.type == TransactionType.INCOME).scalar()  or 0
    total_expense_all = db.query(func.sum(Transaction.amount)).filter(Transaction.type == TransactionType.EXPENSE).scalar() or 0
    cash_on_hand = total_income_all - total_expense_all

    assets = db.query(OtherAsset).all()
    total_assets_current  = sum(a.current_value_vnd  for a in assets)
    total_assets_purchase = sum(a.purchase_price_vnd for a in assets)

    total_projects_paid = db.query(func.sum(ProjectPayment.amount)).filter(
        ProjectPayment.status == PaymentStatus.PAID
    ).scalar() or 0

    net_worth = cash_on_hand + total_savings + total_assets_current + total_projects_paid

    # ── Budget adherence (always today's month, not month-selector) ────────
    today_ym = f"{today.year:04d}-{today.month:02d}"
    try:
        from app.routers.budget import _compute_rows
        budget_rows = _compute_rows(db, today_ym)
    except Exception:
        budget_rows = []

    budget_total     = len(budget_rows)
    alert_over_budget = [r for r in budget_rows if r['available_balance'] < 0]
    budget_adherence_pct = (
        round((budget_total - len(alert_over_budget)) / budget_total * 100)
        if budget_total > 0 else None
    )
    budget_top_cats = sorted(budget_rows, key=lambda r: r['this_month_spent'], reverse=True)[:6]

    # ── Alerts ─────────────────────────────────────────────────────────────
    alert_maturities = db.query(SavingsBundle).filter(
        SavingsBundle.status == SavingsStatus.ACTIVE,
        SavingsBundle.maturity_date.isnot(None),
        SavingsBundle.maturity_date <= today + timedelta(days=30),
    ).order_by(SavingsBundle.maturity_date).all()

    # ── Active projects ────────────────────────────────────────────────────
    active_projects_list = db.query(FinancialProject).filter(
        FinancialProject.status.in_([ProjectStatus.PLANNING, ProjectStatus.IN_PROGRESS])
    ).all()
    active_projects_list.sort(key=lambda p: (p.deadline or date(9999, 12, 31)))

    deadline_cutoff = today + timedelta(days=180)
    at_risk_ids = {
        p.id for p in active_projects_list
        if p.deadline and p.deadline <= deadline_cutoff
        and p.target_amount > 0
        and p.current_amount / p.target_amount < 0.5
    }

    # ── Recent transactions & maturities ──────────────────────────────────
    recent_transactions = db.query(Transaction).order_by(Transaction.date.desc()).limit(10).all()

    upcoming_maturities = db.query(SavingsBundle).filter(
        SavingsBundle.status == SavingsStatus.ACTIVE,
        SavingsBundle.maturity_date.isnot(None),
    ).order_by(SavingsBundle.maturity_date).limit(5).all()

    # ── Expense by category ────────────────────────────────────────────────
    cat_rows = db.query(
        Category.name,
        Category.color,
        func.sum(Transaction.amount).label('total'),
    ).join(Transaction).filter(
        extract('month', Transaction.date) == current_month,
        extract('year',  Transaction.date) == current_year,
        Transaction.type == TransactionType.EXPENSE,
        Transaction.is_savings_related == False,
    ).group_by(Category.id).all()

    cat_rows = sorted(cat_rows, key=lambda x: x[2] or 0, reverse=True)
    expense_by_category = [
        {
            'name':       name,
            'total':      float(total),
            'color':      color,
            'percentage': float(total) / monthly_expense * 100 if monthly_expense > 0 else 0,
        }
        for name, color, total in cat_rows if total and total > 0
    ]

    return {
        "summary": {
            "total_income":          monthly_income,
            "total_expense":         monthly_expense,
            "total_savings_expense": monthly_savings,
            "net_this_month":        monthly_income - monthly_expense - monthly_savings,
            "savings_rate":          savings_rate,
            "net_worth":             net_worth,
            "cash_on_hand":          cash_on_hand,
            "total_savings":         total_savings,
            "total_savings_initial": total_savings_initial,
            "total_assets_current":  total_assets_current,
            "total_assets_purchase": total_assets_purchase,
            "total_assets_count":    len(assets),
            "total_projects_paid":   total_projects_paid,
            "active_projects":       active_projects_count,
            "completed_projects":    completed_projects_count,
            "budget_adherence_pct":  budget_adherence_pct,
            "budget_over_count":     len(alert_over_budget),
            "budget_total":          budget_total,
        },
        "budget_top_cats":      budget_top_cats,
        "alert_maturities":     alert_maturities,
        "alert_over_budget":    alert_over_budget,
        "active_projects_list": active_projects_list,
        "at_risk_ids":          at_risk_ids,
        "today":                today,
        "recent_transactions":  recent_transactions,
        "upcoming_maturities":  upcoming_maturities,
        "expense_by_category":  expense_by_category,
    }


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
    )


@router.get("/dashboard/monthly-trend")
def get_monthly_trend(db: Session = Depends(get_db)):
    today = date.today()
    months = []
    for i in range(11, -1, -1):
        total_months = today.month - 1 - i
        year_num  = today.year + total_months // 12
        month_num = total_months % 12 + 1
        months.append((year_num, month_num))

    start_date = date(months[0][0], months[0][1], 1)

    rows = db.query(
        extract('year',  Transaction.date).label('year'),
        extract('month', Transaction.date).label('month'),
        func.sum(case(
            (Transaction.type == TransactionType.INCOME, Transaction.amount),
            else_=0,
        )).label('income'),
        func.sum(case(
            (and_(Transaction.type == TransactionType.EXPENSE, Transaction.is_savings_related == False), Transaction.amount),
            else_=0,
        )).label('expense'),
        func.sum(case(
            (and_(Transaction.type == TransactionType.EXPENSE, Transaction.is_savings_related == True), Transaction.amount),
            else_=0,
        )).label('savings'),
    ).filter(
        Transaction.date >= start_date
    ).group_by(
        extract('year',  Transaction.date),
        extract('month', Transaction.date),
    ).all()

    data_map = {(int(r.year), int(r.month)): r for r in rows}

    results = []
    for year_num, month_num in months:
        r = data_map.get((year_num, month_num))
        income  = float(r.income)  if r else 0
        expense = float(r.expense) if r else 0
        results.append({
            "month":        date(year_num, month_num, 1).strftime("%b %Y"),
            "income":       income,
            "expense":      expense,
            "savings":      float(r.savings) if r else 0,
            "net":          income - expense,
            "savings_rate": round((income - expense) / income * 100, 1) if income > 0 else 0,
        })

    return results


@router.get("/dashboard/expense-by-category")
def get_expense_by_category(year: Optional[int] = None, month: Optional[int] = None, db: Session = Depends(get_db)):
    today = date.today()
    current_month = month or today.month
    current_year  = year  or today.year

    total_expense = db.query(func.sum(Transaction.amount)).filter(
        extract('month', Transaction.date) == current_month,
        extract('year',  Transaction.date) == current_year,
        Transaction.type == TransactionType.EXPENSE,
        Transaction.is_savings_related == False,
    ).scalar() or 1

    cat_rows = db.query(
        Category.name,
        Category.color,
        func.sum(Transaction.amount),
    ).join(Transaction).filter(
        extract('month', Transaction.date) == current_month,
        extract('year',  Transaction.date) == current_year,
        Transaction.type == TransactionType.EXPENSE,
        Transaction.is_savings_related == False,
    ).group_by(Category.id).all()

    return [
        {
            "category_name":  name,
            "category_color": color,
            "total":          total,
            "percentage":     round(total / total_expense * 100, 1),
        }
        for name, color, total in cat_rows if total > 0
    ]
