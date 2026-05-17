"""Dashboard aggregation service — replaces the inline logic in dashboard.py."""

from datetime import date, timedelta
from calendar import monthrange

from sqlalchemy import func, case, and_
from sqlalchemy.orm import Session

from app.models.database import (
    Category,
    FinancialProject,
    OtherAsset,
    PaymentStatus,
    ProjectPayment,
    ProjectStatus,
    ProjectType,
    SavingsBundle,
    SavingsStatus,
    Transaction,
    TransactionType,
)
from app.services.budget_service import compute_budget_rows


def get_dashboard_data(db: Session, year: int = None, month: int = None) -> dict:
    """Compute all dashboard metrics for the given year/month (defaults to today)."""
    today = date.today()
    current_month = month or today.month
    current_year = year or today.year
    month_start = date(current_year, current_month, 1)
    _, last_day = monthrange(current_year, current_month)
    month_end = date(current_year, current_month, last_day)

    from sqlalchemy import false as sqla_false

    # Split wealth into per-legacy-name buckets so the template/schema keys are preserved
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
    bds_filter = Transaction.category_id.in_(bds_ids) if bds_ids else sqla_false()

    # ── Single-query aggregates ───────────────────────────────────────────────
    _agg = db.query(
        func.sum(
            case(
                (and_(Transaction.type == TransactionType.INCOME,
                      Transaction.date >= month_start, Transaction.date <= month_end,
                      Transaction.deleted_at.is_(None)), Transaction.amount),
                else_=0,
            )
        ).label("monthly_income"),
        func.sum(
            case(
                (and_(Transaction.type == TransactionType.EXPENSE,
                      Transaction.is_savings_related == False,
                      Transaction.date >= month_start, Transaction.date <= month_end,
                      Transaction.deleted_at.is_(None)), Transaction.amount),
                else_=0,
            )
        ).label("monthly_expense"),
        func.sum(
            case(
                (and_(Transaction.type == TransactionType.EXPENSE,
                      Transaction.is_savings_related == True,
                      Transaction.date >= month_start, Transaction.date <= month_end,
                      Transaction.deleted_at.is_(None)), Transaction.amount),
                else_=0,
            )
        ).label("monthly_savings"),
        func.sum(
            case(
                (and_(Transaction.type == TransactionType.EXPENSE,
                      Transaction.date >= month_start, Transaction.date <= month_end,
                      Transaction.deleted_at.is_(None), tk_filter), Transaction.amount),
                else_=0,
            )
        ).label("monthly_tiet_kiem"),
        func.sum(
            case(
                (and_(Transaction.type == TransactionType.EXPENSE,
                      Transaction.date >= month_start, Transaction.date <= month_end,
                      Transaction.deleted_at.is_(None), bds_filter), Transaction.amount),
                else_=0,
            )
        ).label("monthly_bds"),
        func.sum(
            case(
                (and_(Transaction.type == TransactionType.INCOME,
                      Transaction.deleted_at.is_(None)), Transaction.amount),
                else_=0,
            )
        ).label("total_income"),
        func.sum(
            case(
                (and_(Transaction.type == TransactionType.EXPENSE,
                      Transaction.deleted_at.is_(None)), Transaction.amount),
                else_=0,
            )
        ).label("total_expense"),
    ).first()

    monthly_income = float(_agg.monthly_income or 0)
    monthly_expense = float(_agg.monthly_expense or 0)
    monthly_savings = float(_agg.monthly_savings or 0)
    monthly_tiet_kiem = float(_agg.monthly_tiet_kiem or 0)
    monthly_bds = float(_agg.monthly_bds or 0)
    monthly_wealth_building = monthly_tiet_kiem + monthly_bds

    # Savings rate: % of income that went to wealth-building categories
    savings_rate = round(monthly_wealth_building / monthly_income * 100, 1) if monthly_income > 0 else 0

    # ── Project amounts by type ───────────────────────────────────────────────
    _PROJECT_TYPE_META = {
        "real_estate": {"label": "Bất động sản", "color": "#10b981"},
        "investment": {"label": "Investment", "color": "#6366f1"},
    }
    _type_rows = (
        db.query(FinancialProject.type, func.sum(FinancialProject.current_amount).label("total"))
        .filter(
            FinancialProject.type.in_([ProjectType.REAL_ESTATE, ProjectType.INVESTMENT]),
            FinancialProject.deleted_at.is_(None),
        )
        .group_by(FinancialProject.type)
        .all()
    )
    project_amounts_by_type = [
        {
            "label": _PROJECT_TYPE_META[row.type.value]["label"],
            "color": _PROJECT_TYPE_META[row.type.value]["color"],
            "amount": float(row.total or 0),
        }
        for row in _type_rows
        if (row.total or 0) > 0
    ]

    # ── Static / current-state figures ───────────────────────────────────────
    savings_data = (
        db.query(func.sum(SavingsBundle.future_amount), func.sum(SavingsBundle.initial_deposit))
        .filter(SavingsBundle.status == SavingsStatus.ACTIVE, SavingsBundle.deleted_at.is_(None))
        .first()
    )
    total_savings = float(savings_data[0] or 0) if savings_data else 0
    total_savings_initial = float(savings_data[1] or 0) if savings_data else 0

    active_projects_count = (
        db.query(FinancialProject)
        .filter(
            FinancialProject.status.in_([ProjectStatus.PLANNING, ProjectStatus.IN_PROGRESS]),
            FinancialProject.deleted_at.is_(None),
        )
        .count()
    )
    completed_projects_count = (
        db.query(FinancialProject)
        .filter(FinancialProject.status == ProjectStatus.COMPLETED, FinancialProject.deleted_at.is_(None))
        .count()
    )

    cash_on_hand = float((_agg.total_income or 0) - (_agg.total_expense or 0))

    assets = db.query(OtherAsset).all()
    total_assets_current = sum(a.current_value_vnd for a in assets)
    total_assets_purchase = sum(a.purchase_price_vnd for a in assets)

    total_projects_paid = (
        db.query(func.sum(ProjectPayment.amount)).filter(ProjectPayment.status == PaymentStatus.PAID).scalar() or 0
    )

    net_worth = cash_on_hand + total_savings + total_assets_current + total_projects_paid

    # ── Budget adherence ──────────────────────────────────────────────────────
    today_ym = f"{today.year:04d}-{today.month:02d}"
    try:
        budget_rows = compute_budget_rows(db, today_ym)
    except Exception:
        budget_rows = []

    budget_total = len(budget_rows)
    alert_over_budget = [r for r in budget_rows if r["available_balance"] < 0]
    _total_allocated = sum(r["cumulative_allocated"] for r in budget_rows)
    _on_track_allocated = sum(r["cumulative_allocated"] for r in budget_rows if r["available_balance"] >= 0)
    budget_adherence_pct = round(_on_track_allocated / _total_allocated * 100) if _total_allocated > 0 else None
    budget_top_cats = sorted(budget_rows, key=lambda r: r["this_month_spent"], reverse=True)[:6]

    # ── Unsettled advances ────────────────────────────────────────────────────
    _adv = (
        db.query(func.count(Transaction.id), func.sum(Transaction.amount))
        .filter(Transaction.is_advance == True, Transaction.advance_settled == False,  # noqa: E712
                Transaction.deleted_at.is_(None))
        .first()
    )
    unsettled_advance_count = int(_adv[0] or 0)
    unsettled_advance_total = float(_adv[1] or 0)

    # ── Alerts ────────────────────────────────────────────────────────────────
    alert_maturities = (
        db.query(SavingsBundle)
        .filter(
            SavingsBundle.status == SavingsStatus.ACTIVE,
            SavingsBundle.deleted_at.is_(None),
            SavingsBundle.maturity_date.isnot(None),
            SavingsBundle.maturity_date <= today + timedelta(days=30),
        )
        .order_by(SavingsBundle.maturity_date)
        .all()
    )

    # ── Active projects ───────────────────────────────────────────────────────
    active_projects_list = (
        db.query(FinancialProject)
        .filter(
            FinancialProject.status.in_([ProjectStatus.PLANNING, ProjectStatus.IN_PROGRESS]),
            FinancialProject.deleted_at.is_(None),
        )
        .all()
    )
    active_projects_list.sort(key=lambda p: p.deadline or date(9999, 12, 31))

    deadline_cutoff = today + timedelta(days=180)
    at_risk_ids = {
        p.id
        for p in active_projects_list
        if p.deadline
        and p.deadline <= deadline_cutoff
        and p.target_amount > 0
        and p.current_amount / p.target_amount < 0.5
    }

    # ── Recent transactions & maturities ─────────────────────────────────────
    recent_transactions = (
        db.query(Transaction)
        .filter(Transaction.deleted_at.is_(None))
        .order_by(Transaction.date.desc())
        .limit(10)
        .all()
    )

    upcoming_maturities = (
        db.query(SavingsBundle)
        .filter(
            SavingsBundle.status == SavingsStatus.ACTIVE,
            SavingsBundle.deleted_at.is_(None),
            SavingsBundle.maturity_date.isnot(None),
        )
        .order_by(SavingsBundle.maturity_date)
        .limit(5)
        .all()
    )

    # ── Expense by category ───────────────────────────────────────────────────
    cat_rows = (
        db.query(Category.name, Category.color, func.sum(Transaction.amount).label("total"))
        .join(Transaction)
        .filter(
            Transaction.date >= month_start,
            Transaction.date <= month_end,
            Transaction.type == TransactionType.EXPENSE,
            Transaction.is_savings_related == False,  # noqa: E712
            Transaction.deleted_at.is_(None),
        )
        .group_by(Category.id)
        .all()
    )
    cat_rows = sorted(cat_rows, key=lambda x: x[2] or 0, reverse=True)
    expense_by_category = [
        {
            "name": name,
            "total": float(total),
            "color": color,
            "percentage": float(total) / monthly_expense * 100 if monthly_expense > 0 else 0,
        }
        for name, color, total in cat_rows
        if total and total > 0
    ]

    return {
        "summary": {
            "total_income": monthly_income,
            "total_expense": monthly_expense,
            "total_savings_expense": monthly_savings,
            "net_this_month": monthly_income - monthly_expense - monthly_savings,
            "savings_rate": savings_rate,
            "net_worth": net_worth,
            "cash_on_hand": cash_on_hand,
            "total_savings": total_savings,
            "total_savings_initial": total_savings_initial,
            "total_assets_current": total_assets_current,
            "total_assets_purchase": total_assets_purchase,
            "total_assets_count": len(assets),
            "total_projects_paid": total_projects_paid,
            "active_projects": active_projects_count,
            "completed_projects": completed_projects_count,
            "budget_adherence_pct": budget_adherence_pct,
            "budget_over_count": len(alert_over_budget),
            "budget_total": budget_total,
            "monthly_tiet_kiem": monthly_tiet_kiem,
            "monthly_bds": monthly_bds,
        },
        "budget_top_cats": budget_top_cats,
        "alert_maturities": alert_maturities,
        "alert_over_budget": alert_over_budget,
        "active_projects_list": active_projects_list,
        "at_risk_ids": at_risk_ids,
        "today": today,
        "unsettled_advance_count": unsettled_advance_count,
        "unsettled_advance_total": unsettled_advance_total,
        "recent_transactions": recent_transactions,
        "upcoming_maturities": upcoming_maturities,
        "expense_by_category": expense_by_category,
        "project_amounts_by_type": project_amounts_by_type,
    }
