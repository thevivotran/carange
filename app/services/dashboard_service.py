"""Dashboard aggregation service — replaces the inline logic in dashboard.py."""

import time
import threading
from datetime import date, timedelta
from calendar import monthrange
from types import SimpleNamespace
from typing import Any

from sqlalchemy import func, case, and_
from sqlalchemy.orm import Session, joinedload

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
from app.services.settings_service import get_setting

# ── In-memory dashboard cache (TTL = 120 s) ──────────────────────────────────
_CACHE_TTL = 120.0
_cache: dict[tuple, tuple[float, Any]] = {}
_cache_lock = threading.Lock()


def invalidate_dashboard_cache() -> None:
    """Clear the dashboard cache — call after any write that affects dashboard metrics."""
    with _cache_lock:
        _cache.clear()


def _cache_get(key: tuple) -> Any | None:
    with _cache_lock:
        entry = _cache.get(key)
    if entry is None:
        return None
    ts, value = entry
    if time.monotonic() - ts > _CACHE_TTL:
        with _cache_lock:
            _cache.pop(key, None)
        return None
    return value


def _cache_set(key: tuple, value: Any) -> None:
    with _cache_lock:
        _cache[key] = (time.monotonic(), value)


def _project_ns(p) -> SimpleNamespace:
    ns = SimpleNamespace()
    ns.id = p.id
    ns.name = p.name
    ns.current_amount = p.current_amount
    ns.target_amount = p.target_amount
    ns.deadline = p.deadline
    return ns


def _savings_ns(s) -> SimpleNamespace:
    ns = SimpleNamespace()
    ns.name = s.name
    ns.bank_name = s.bank_name
    ns.maturity_date = s.maturity_date
    ns.future_amount = s.future_amount
    ns.current_amount = s.current_amount
    return ns


def _payment_ns(p) -> SimpleNamespace:
    ns = SimpleNamespace()
    ns.amount = p.amount
    ns.due_date = p.due_date
    return ns


def _txn_ns(t) -> SimpleNamespace:
    ns = SimpleNamespace()
    ns.description = t.description
    ns.date = t.date
    ns.type = t.type
    ns.amount = t.amount
    ns.category = SimpleNamespace(name=t.category.name if t.category else "")
    return ns


def get_dashboard_data(db: Session, year: int = None, month: int = None) -> dict:
    """Compute all dashboard metrics for the given year/month (defaults to today).

    Results are cached in-memory for _CACHE_TTL seconds, keyed by (year, month).
    Call invalidate_dashboard_cache() after any write that mutates dashboard data.
    """
    today = date.today()
    current_month = month or today.month
    current_year = year or today.year

    _cache_key = (current_year, current_month)
    cached = _cache_get(_cache_key)
    if cached is not None:
        return cached
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
                (
                    and_(
                        Transaction.type == TransactionType.INCOME,
                        Transaction.is_savings_related == False,
                        Transaction.date >= month_start,
                        Transaction.date <= month_end,
                        Transaction.deleted_at.is_(None),
                    ),
                    Transaction.amount,
                ),
                else_=0,
            )
        ).label("monthly_income"),
        func.sum(
            case(
                (
                    and_(
                        Transaction.type == TransactionType.EXPENSE,
                        Transaction.is_savings_related == False,
                        Transaction.date >= month_start,
                        Transaction.date <= month_end,
                        Transaction.deleted_at.is_(None),
                    ),
                    Transaction.amount,
                ),
                else_=0,
            )
        ).label("monthly_expense"),
        func.sum(
            case(
                (
                    and_(
                        Transaction.type == TransactionType.EXPENSE,
                        Transaction.is_savings_related == True,
                        Transaction.date >= month_start,
                        Transaction.date <= month_end,
                        Transaction.deleted_at.is_(None),
                    ),
                    Transaction.amount,
                ),
                else_=0,
            )
        ).label("monthly_savings"),
        func.sum(
            case(
                (
                    and_(
                        Transaction.type == TransactionType.EXPENSE,
                        Transaction.date >= month_start,
                        Transaction.date <= month_end,
                        Transaction.deleted_at.is_(None),
                        tk_filter,
                    ),
                    Transaction.amount,
                ),
                else_=0,
            )
        ).label("monthly_tiet_kiem"),
        func.sum(
            case(
                (
                    and_(
                        Transaction.type == TransactionType.EXPENSE,
                        Transaction.date >= month_start,
                        Transaction.date <= month_end,
                        Transaction.deleted_at.is_(None),
                        bds_filter,
                    ),
                    Transaction.amount,
                ),
                else_=0,
            )
        ).label("monthly_bds"),
        func.sum(
            case(
                (
                    and_(
                        Transaction.type == TransactionType.INCOME,
                        Transaction.is_savings_related == False,
                        Transaction.deleted_at.is_(None),
                    ),
                    Transaction.amount,
                ),
                else_=0,
            )
        ).label("total_income"),
        func.sum(
            case(
                (
                    and_(Transaction.type == TransactionType.EXPENSE, Transaction.deleted_at.is_(None)),
                    Transaction.amount,
                ),
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

    savings_rate = round(monthly_wealth_building / monthly_income * 100, 1) if monthly_income > 0 else 0
    liquid_savings_rate = round(monthly_tiet_kiem / monthly_income * 100, 1) if monthly_income > 0 else 0
    bds_rate = round(monthly_bds / monthly_income * 100, 1) if monthly_income > 0 else 0
    living_expense_ratio = round(monthly_expense / monthly_income * 100, 1) if monthly_income > 0 else 0

    # ── Prev-month aggregates (delta arrows) ─────────────────────────────────
    if current_month == 1:
        prev_year_num, prev_month_num = current_year - 1, 12
    else:
        prev_year_num, prev_month_num = current_year, current_month - 1
    prev_month_start = date(prev_year_num, prev_month_num, 1)
    _, _prev_last = monthrange(prev_year_num, prev_month_num)
    prev_month_end = date(prev_year_num, prev_month_num, _prev_last)

    _prev = db.query(
        func.sum(
            case(
                (
                    and_(
                        Transaction.type == TransactionType.INCOME,
                        Transaction.is_savings_related == False,
                        Transaction.date >= prev_month_start,
                        Transaction.date <= prev_month_end,
                        Transaction.deleted_at.is_(None),
                    ),
                    Transaction.amount,
                ),
                else_=0,
            )
        ).label("income"),
        func.sum(
            case(
                (
                    and_(
                        Transaction.type == TransactionType.EXPENSE,
                        Transaction.is_savings_related == False,
                        Transaction.date >= prev_month_start,
                        Transaction.date <= prev_month_end,
                        Transaction.deleted_at.is_(None),
                    ),
                    Transaction.amount,
                ),
                else_=0,
            )
        ).label("expense"),
        func.sum(
            case(
                (
                    and_(
                        Transaction.type == TransactionType.EXPENSE,
                        Transaction.is_savings_related == True,
                        Transaction.date >= prev_month_start,
                        Transaction.date <= prev_month_end,
                        Transaction.deleted_at.is_(None),
                    ),
                    Transaction.amount,
                ),
                else_=0,
            )
        ).label("savings"),
        func.sum(
            case(
                (
                    and_(
                        Transaction.type == TransactionType.EXPENSE,
                        Transaction.date >= prev_month_start,
                        Transaction.date <= prev_month_end,
                        Transaction.deleted_at.is_(None),
                        tk_filter,
                    ),
                    Transaction.amount,
                ),
                else_=0,
            )
        ).label("tiet_kiem"),
        func.sum(
            case(
                (
                    and_(
                        Transaction.type == TransactionType.EXPENSE,
                        Transaction.date >= prev_month_start,
                        Transaction.date <= prev_month_end,
                        Transaction.deleted_at.is_(None),
                        bds_filter,
                    ),
                    Transaction.amount,
                ),
                else_=0,
            )
        ).label("bds"),
    ).first()

    _pi = float(_prev.income or 0)
    _pe = float(_prev.expense or 0)
    _ps = float(_prev.savings or 0)
    prev_liquid_savings_rate = round(float(_prev.tiet_kiem or 0) / _pi * 100, 1) if _pi > 0 else 0
    prev_bds_rate = round(float(_prev.bds or 0) / _pi * 100, 1) if _pi > 0 else 0
    prev_net_cash = _pi - _pe - _ps
    prev_living_expense_ratio = round(_pe / _pi * 100, 1) if _pi > 0 else 0

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

    # ── Emergency fund coverage ───────────────────────────────────────────────
    # Avg living expense over last 3 completed months (not counting current month)
    _ef_m, _ef_y = prev_month_num, prev_year_num
    _ef_sm = _ef_m - 2
    _ef_sy = _ef_y
    if _ef_sm <= 0:
        _ef_sm += 12
        _ef_sy -= 1
    _ef_start = date(_ef_sy, _ef_sm, 1)
    _ef_end = prev_month_end
    _ef_total = float(
        db.query(func.sum(Transaction.amount))
        .filter(
            Transaction.type == TransactionType.EXPENSE,
            Transaction.is_savings_related == False,  # noqa: E712
            Transaction.date >= _ef_start,
            Transaction.date <= _ef_end,
            Transaction.deleted_at.is_(None),
        )
        .scalar()
        or 0
    )
    avg_monthly_expense = _ef_total / 3 if _ef_total > 0 else monthly_expense or 1
    emergency_fund_months = round(total_savings / avg_monthly_expense, 1) if avg_monthly_expense > 0 else 0

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
    # Sort by risk: most over-budget first, then highest usage %, then highest spend
    budget_top_cats = sorted(
        budget_rows,
        key=lambda r: (r["available_balance"], -(r["cumulative_pct"])),
    )[:6]

    # ── Unsettled advances ────────────────────────────────────────────────────
    _adv = (
        db.query(func.count(Transaction.id), func.sum(Transaction.amount))
        .filter(
            Transaction.is_advance == True,
            Transaction.advance_settled == False,  # noqa: E712
            Transaction.deleted_at.is_(None),
        )
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

    # ── BDS project details ───────────────────────────────────────────────────
    bds_project = next(
        (p for p in active_projects_list if p.type == ProjectType.REAL_ESTATE),
        None,
    )
    bds_next_payment = None
    bds_ytd_paid = 0.0
    bds_ytd_planned = 0.0
    bds_completion_date = None
    bds_days_until_next = None

    if bds_project:
        bds_next_payment = (
            db.query(ProjectPayment)
            .filter(
                ProjectPayment.project_id == bds_project.id,
                ProjectPayment.status == PaymentStatus.PENDING,
                ProjectPayment.due_date.isnot(None),
            )
            .order_by(ProjectPayment.due_date)
            .first()
        )
        if bds_next_payment:
            bds_days_until_next = (bds_next_payment.due_date - today).days

        _year_start = date(current_year, 1, 1)
        _year_end = date(current_year, 12, 31)
        bds_ytd_paid = float(
            db.query(func.sum(ProjectPayment.amount))
            .filter(
                ProjectPayment.project_id == bds_project.id,
                ProjectPayment.status == PaymentStatus.PAID,
                ProjectPayment.due_date >= _year_start,
                ProjectPayment.due_date <= _year_end,
            )
            .scalar()
            or 0
        )
        bds_ytd_planned = float(
            db.query(func.sum(ProjectPayment.amount))
            .filter(
                ProjectPayment.project_id == bds_project.id,
                ProjectPayment.due_date >= _year_start,
                ProjectPayment.due_date <= _year_end,
            )
            .scalar()
            or 0
        )
        _last_pmt = (
            db.query(ProjectPayment)
            .filter(
                ProjectPayment.project_id == bds_project.id,
                ProjectPayment.due_date.isnot(None),
            )
            .order_by(ProjectPayment.due_date.desc())
            .first()
        )
        bds_completion_date = _last_pmt.due_date if _last_pmt else None

    # ── One-income stress test ────────────────────────────────────────────────
    bds_monthly_installment = (bds_next_payment.amount if bds_next_payment else monthly_bds) or 0
    stress_test_required = avg_monthly_expense + 20_000_000 + bds_monthly_installment
    stress_test_cushion = monthly_income - stress_test_required

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
        .options(joinedload(Transaction.category))
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

    # ── Settings-powered metrics ──────────────────────────────────────────────
    savings_target_pct = float(get_setting(db, "savings_target_pct", "25") or 25)

    _fi_raw = get_setting(db, "fi_target_vnd")
    fi_target_vnd = float(_fi_raw) if _fi_raw else None
    fi_progress_pct = round(net_worth / fi_target_vnd * 100, 1) if fi_target_vnd else None

    runway_months = round((cash_on_hand + total_savings) / avg_monthly_expense, 1) if avg_monthly_expense > 0 else 0

    # Net worth 1 month ago — reuse same cumulative income/expense logic for prior month snapshot
    # We do a lightweight query: cumulative income - cumulative expense at end of prev month
    _prev_cum_income = float(
        db.query(func.sum(Transaction.amount))
        .filter(
            Transaction.type == TransactionType.INCOME,
            Transaction.is_savings_related == False,  # noqa: E712
            Transaction.date <= prev_month_end,
            Transaction.deleted_at.is_(None),
        )
        .scalar()
        or 0
    )
    _prev_cum_expense = float(
        db.query(func.sum(Transaction.amount))
        .filter(
            Transaction.type == TransactionType.EXPENSE,
            Transaction.date <= prev_month_end,
            Transaction.deleted_at.is_(None),
        )
        .scalar()
        or 0
    )
    _prev_savings_total = float(
        db.query(func.sum(SavingsBundle.future_amount))
        .filter(SavingsBundle.status == SavingsStatus.ACTIVE, SavingsBundle.deleted_at.is_(None))
        .scalar()
        or 0
    )
    _prev_assets_total = sum(a.current_value_vnd for a in assets)
    _prev_proj_paid = float(
        db.query(func.sum(ProjectPayment.amount)).filter(ProjectPayment.status == PaymentStatus.PAID).scalar() or 0
    )
    net_worth_1mo_ago = (
        (_prev_cum_income - _prev_cum_expense) + _prev_savings_total + _prev_assets_total + _prev_proj_paid
    )
    net_worth_growth_rate = (
        round((net_worth - net_worth_1mo_ago) / abs(net_worth_1mo_ago) * 100, 1) if net_worth_1mo_ago else 0
    )

    # Passive income
    passive_cat_ids = [
        r[0]
        for r in db.query(Category.id)
        .filter(Category.is_passive_income == True, Category.type == TransactionType.INCOME)  # noqa: E712
        .all()
    ]
    passive_income_monthly = (
        float(
            db.query(func.sum(Transaction.amount))
            .filter(
                Transaction.type == TransactionType.INCOME,
                Transaction.category_id.in_(passive_cat_ids),
                Transaction.date >= month_start,
                Transaction.date <= month_end,
                Transaction.deleted_at.is_(None),
            )
            .scalar()
            or 0
        )
        if passive_cat_ids
        else 0.0
    )
    passive_income_pct = round(passive_income_monthly / monthly_income * 100, 1) if monthly_income > 0 else 0.0

    # Baby fund bundle
    _bf_raw = get_setting(db, "baby_fund_bundle_id")
    baby_fund_bundle = None
    if _bf_raw:
        baby_fund_bundle = (
            db.query(SavingsBundle).filter(SavingsBundle.id == int(_bf_raw), SavingsBundle.deleted_at.is_(None)).first()
        )

    result = {
        "summary": {
            "total_income": monthly_income,
            "total_expense": monthly_expense,
            "total_savings_expense": monthly_savings,
            "net_this_month": monthly_income - monthly_expense - monthly_savings,
            "savings_rate": savings_rate,
            "liquid_savings_rate": liquid_savings_rate,
            "bds_rate": bds_rate,
            "living_expense_ratio": living_expense_ratio,
            "emergency_fund_months": emergency_fund_months,
            "avg_monthly_expense": avg_monthly_expense,
            "prev_liquid_savings_rate": prev_liquid_savings_rate,
            "prev_bds_rate": prev_bds_rate,
            "prev_net_cash": prev_net_cash,
            "prev_living_expense_ratio": prev_living_expense_ratio,
            "stress_test_required": stress_test_required,
            "stress_test_cushion": stress_test_cushion,
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
            "savings_target_pct": savings_target_pct,
            "fi_target_vnd": fi_target_vnd,
            "fi_progress_pct": fi_progress_pct,
            "runway_months": runway_months,
            "net_worth_growth_rate": net_worth_growth_rate,
            "passive_income_monthly": passive_income_monthly,
            "passive_income_pct": passive_income_pct,
        },
        "budget_top_cats": budget_top_cats,
        "alert_maturities": [_savings_ns(s) for s in alert_maturities],
        "alert_over_budget": alert_over_budget,
        "active_projects_list": [_project_ns(p) for p in active_projects_list],
        "at_risk_ids": at_risk_ids,
        "today": today,
        "unsettled_advance_count": unsettled_advance_count,
        "unsettled_advance_total": unsettled_advance_total,
        "recent_transactions": [_txn_ns(t) for t in recent_transactions],
        "upcoming_maturities": [_savings_ns(s) for s in upcoming_maturities],
        "expense_by_category": expense_by_category,
        "project_amounts_by_type": project_amounts_by_type,
        "bds_project": _project_ns(bds_project) if bds_project else None,
        "bds_next_payment": _payment_ns(bds_next_payment) if bds_next_payment else None,
        "bds_ytd_paid": bds_ytd_paid,
        "bds_ytd_planned": bds_ytd_planned,
        "bds_completion_date": bds_completion_date,
        "bds_days_until_next": bds_days_until_next,
        "baby_fund_bundle": _savings_ns(baby_fund_bundle) if baby_fund_bundle else None,
    }
    _cache_set(_cache_key, result)
    return result
