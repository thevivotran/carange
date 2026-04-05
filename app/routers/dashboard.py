from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func, extract, case, and_
from datetime import datetime, date
from typing import List, Optional

from app.models.database import get_db, Transaction, Category, SavingsBundle, FinancialProject, SavingsStatus, ProjectStatus, TransactionType, OtherAsset, ProjectPayment, PaymentStatus
from app.models.schemas import DashboardData, DashboardSummary, MonthlyData, CategorySummary

router = APIRouter()


def get_dashboard_page_data(db: Session, year: int = None, month: int = None) -> dict:
    """Shared helper used by both the HTML page route and the API summary endpoint."""
    today = date.today()
    current_month = month or today.month
    current_year = year or today.year

    monthly_income = db.query(func.sum(Transaction.amount)).filter(
        extract('month', Transaction.date) == current_month,
        extract('year', Transaction.date) == current_year,
        Transaction.type == TransactionType.INCOME
    ).scalar() or 0

    monthly_expense = db.query(func.sum(Transaction.amount)).filter(
        extract('month', Transaction.date) == current_month,
        extract('year', Transaction.date) == current_year,
        Transaction.type == TransactionType.EXPENSE,
        Transaction.is_savings_related == False
    ).scalar() or 0

    monthly_savings = db.query(func.sum(Transaction.amount)).filter(
        extract('month', Transaction.date) == current_month,
        extract('year', Transaction.date) == current_year,
        Transaction.type == TransactionType.EXPENSE,
        Transaction.is_savings_related == True
    ).scalar() or 0

    savings_data = db.query(
        func.sum(SavingsBundle.future_amount),
        func.sum(SavingsBundle.initial_deposit)
    ).filter(SavingsBundle.status == SavingsStatus.ACTIVE).first()

    total_savings = (savings_data[0] or 0) if savings_data else 0
    total_savings_initial = (savings_data[1] or 0) if savings_data else 0

    active_projects_count = db.query(FinancialProject).filter(
        FinancialProject.status.in_([ProjectStatus.PLANNING, ProjectStatus.IN_PROGRESS])
    ).count()

    completed_projects_count = db.query(FinancialProject).filter(
        FinancialProject.status == ProjectStatus.COMPLETED
    ).count()

    total_income_all_time = db.query(func.sum(Transaction.amount)).filter(
        Transaction.type == TransactionType.INCOME
    ).scalar() or 0

    total_expense_all_time = db.query(func.sum(Transaction.amount)).filter(
        Transaction.type == TransactionType.EXPENSE
    ).scalar() or 0

    cash_on_hand = total_income_all_time - total_expense_all_time

    assets = db.query(OtherAsset).all()
    total_assets_current = sum(a.current_value_vnd for a in assets)
    total_assets_purchase = sum(a.purchase_price_vnd for a in assets)
    total_assets_count = len(assets)

    total_projects_paid = db.query(func.sum(ProjectPayment.amount)).filter(
        ProjectPayment.status == PaymentStatus.PAID
    ).scalar() or 0

    recent_transactions = db.query(Transaction).order_by(Transaction.date.desc()).limit(5).all()

    upcoming_maturities = db.query(SavingsBundle).filter(
        SavingsBundle.status == SavingsStatus.ACTIVE,
        SavingsBundle.maturity_date != None
    ).order_by(SavingsBundle.maturity_date).limit(5).all()

    category_data = db.query(
        Category.name,
        Category.color,
        func.sum(Transaction.amount).label('total')
    ).join(Transaction).filter(
        extract('month', Transaction.date) == current_month,
        extract('year', Transaction.date) == current_year,
        Transaction.type == TransactionType.EXPENSE,
        Transaction.is_savings_related == False
    ).group_by(Category.id).all()

    category_data = sorted(category_data, key=lambda x: x[2] if x[2] else 0, reverse=True)

    expense_by_category = [
        {
            'name': name,
            'total': float(total),
            'color': color,
            'percentage': (float(total) / monthly_expense * 100) if monthly_expense > 0 else 0
        }
        for name, color, total in category_data if total and total > 0
    ]

    return {
        "summary": {
            "total_income": monthly_income,
            "total_expense": monthly_expense,
            "total_savings_expense": monthly_savings,
            "net_this_month": monthly_income - monthly_expense - monthly_savings,
            "cash_on_hand": cash_on_hand,
            "total_savings": total_savings,
            "total_savings_initial": total_savings_initial,
            "total_assets_current": total_assets_current,
            "total_assets_purchase": total_assets_purchase,
            "total_assets_count": total_assets_count,
            "total_projects_paid": total_projects_paid,
            "active_projects": active_projects_count,
            "completed_projects": completed_projects_count,
        },
        "recent_transactions": recent_transactions,
        "upcoming_maturities": upcoming_maturities,
        "expense_by_category": expense_by_category,
    }


@router.get("/dashboard/summary", response_model=DashboardSummary)
def get_dashboard_summary(year: Optional[int] = None, month: Optional[int] = None, db: Session = Depends(get_db)):
    data = get_dashboard_page_data(db, year=year, month=month)
    s = data["summary"]
    return DashboardSummary(
        total_income_month=s["total_income"],
        total_expense_month=s["total_expense"],
        total_savings_month=s["total_savings_expense"],
        net_this_month=s["net_this_month"],
        cash_on_hand=s["cash_on_hand"],
        total_savings_active=s["total_savings"],
        total_savings_target=s["total_savings_initial"],
        total_assets_current=s["total_assets_current"],
        total_assets_purchase=s["total_assets_purchase"],
        total_assets_count=s["total_assets_count"],
        total_projects_paid=s["total_projects_paid"],
        active_projects_count=s["active_projects"],
        completed_projects_count=s["completed_projects"],
    )

@router.get("/dashboard/monthly-trend")
def get_monthly_trend(db: Session = Depends(get_db)):
    # Build the ordered list of the last 12 months
    today = date.today()
    months = []
    for i in range(11, -1, -1):
        total_months = today.month - 1 - i
        year_num = today.year + total_months // 12
        month_num = total_months % 12 + 1
        months.append((year_num, month_num))

    start_date = date(months[0][0], months[0][1], 1)

    # Single query: sum income, regular expense, and savings expense grouped by year+month
    rows = db.query(
        extract('year', Transaction.date).label('year'),
        extract('month', Transaction.date).label('month'),
        func.sum(case(
            (Transaction.type == TransactionType.INCOME, Transaction.amount),
            else_=0
        )).label('income'),
        func.sum(case(
            (and_(Transaction.type == TransactionType.EXPENSE, Transaction.is_savings_related == False), Transaction.amount),
            else_=0
        )).label('expense'),
        func.sum(case(
            (and_(Transaction.type == TransactionType.EXPENSE, Transaction.is_savings_related == True), Transaction.amount),
            else_=0
        )).label('savings'),
    ).filter(
        Transaction.date >= start_date
    ).group_by(
        extract('year', Transaction.date),
        extract('month', Transaction.date)
    ).all()

    # Index results by (year, month) for fast lookup
    data_map = {(int(r.year), int(r.month)): r for r in rows}

    results = []
    for year_num, month_num in months:
        r = data_map.get((year_num, month_num))
        results.append({
            "month": date(year_num, month_num, 1).strftime("%b %Y"),
            "income": float(r.income) if r else 0,
            "expense": float(r.expense) if r else 0,
            "savings": float(r.savings) if r else 0,
        })

    return results

@router.get("/dashboard/expense-by-category")
def get_expense_by_category(year: Optional[int] = None, month: Optional[int] = None, db: Session = Depends(get_db)):
    today = date.today()
    current_month = month or today.month
    current_year = year or today.year

    # Get total non-savings expense for current month
    total_expense = db.query(func.sum(Transaction.amount)).filter(
        extract('month', Transaction.date) == current_month,
        extract('year', Transaction.date) == current_year,
        Transaction.type == TransactionType.EXPENSE,
        Transaction.is_savings_related == False
    ).scalar() or 1  # Avoid division by zero

    # Get expense by category (excluding savings-related)
    category_data = db.query(
        Category.name,
        Category.color,
        func.sum(Transaction.amount)
    ).join(Transaction).filter(
        extract('month', Transaction.date) == current_month,
        extract('year', Transaction.date) == current_year,
        Transaction.type == TransactionType.EXPENSE,
        Transaction.is_savings_related == False
    ).group_by(Category.id).all()

    return [
        {
            "category_name": name,
            "category_color": color,
            "total": total,
            "percentage": round((total / total_expense) * 100, 1)
        }
        for name, color, total in category_data if total > 0
    ]

@router.get("/dashboard/upcoming-maturities")
def get_upcoming_maturities(limit: int = 5, db: Session = Depends(get_db)):
    bundles = db.query(SavingsBundle).filter(
        SavingsBundle.status == SavingsStatus.ACTIVE,
        SavingsBundle.maturity_date != None
    ).order_by(SavingsBundle.maturity_date).limit(limit).all()
    
    return bundles

@router.get("/dashboard/recent-transactions")
def get_recent_transactions(limit: int = 5, db: Session = Depends(get_db)):
    transactions = db.query(Transaction).order_by(
        Transaction.date.desc()
    ).limit(limit).all()
    
    return transactions