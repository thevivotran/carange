"""Dashboard aggregation service — replaces the inline logic in dashboard.py."""

import logging
import time
import threading
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

log = logging.getLogger("app.dashboard_service")

from sqlalchemy import func, case, and_, text
from sqlalchemy.orm import Session, joinedload

from app.models.database import (
    Category,
    DATABASE_URL,
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
from app.services.fiscal_period import (
    current_period_ym,
    fiscal_window_ym,
    get_month_start_day,
    shift_period_ym,
)
from app.services.settings_service import get_setting

_USE_MATVIEW = DATABASE_URL.startswith("postgresql") or DATABASE_URL.startswith("postgres")

# ── Dashboard cache ───────────────────────────────────────────────────────────
# In-memory dict for fast within-pod serving. Cross-pod invalidation (by the
# OCR/email workers running in separate k8s pods) uses a sentinel row written
# to period_rollups so the main app detects external mutations on the next
# cache hit check.
#
# Cache entry format: (monotonic_ts, wall_ts_float, value)
# wall_ts_float is datetime.now(utc).timestamp() at the time of the set, used
# to compare against the DB sentinel's computed_at.

_CACHE_TTL = 120.0
_cache: dict[tuple, tuple[float, float, Any]] = {}
_cache_lock = threading.Lock()

_SENTINEL_HORIZON = "__inv__"
_SENTINEL_KEY = "global"


def _db_get_sentinel_wall_ts(db: Session) -> float | None:
    """Return the sentinel's computed_at as a UTC timestamp float, or None if absent."""
    try:
        from app.models.database import PeriodRollup

        row = (
            db.query(PeriodRollup.computed_at)
            .filter(
                PeriodRollup.horizon == _SENTINEL_HORIZON,
                PeriodRollup.period_key == _SENTINEL_KEY,
            )
            .scalar()
        )
        if row is None:
            return None
        if row.tzinfo is None:
            row = row.replace(tzinfo=timezone.utc)
        return row.timestamp()
    except Exception:
        return None


def _db_write_sentinel(db: Session) -> None:
    """Upsert the invalidation sentinel with computed_at = now."""
    try:
        from app.models.database import PeriodRollup

        now = datetime.now(timezone.utc)
        row = (
            db.query(PeriodRollup)
            .filter(
                PeriodRollup.horizon == _SENTINEL_HORIZON,
                PeriodRollup.period_key == _SENTINEL_KEY,
            )
            .first()
        )
        if row is None:
            db.add(
                PeriodRollup(
                    horizon=_SENTINEL_HORIZON,
                    period_key=_SENTINEL_KEY,
                    payload_json={},
                    computed_at=now,
                )
            )
        else:
            row.computed_at = now
        db.commit()
    except Exception:
        db.rollback()


def _fetch_matview_rows(db: Session) -> list | None:
    """Fetch all rows from mv_monthly_totals in one shot.

    Returns None if the view is unavailable (e.g. migration not yet applied),
    triggering the ORM fallback path in get_dashboard_data().
    """
    try:
        rows = (
            db.execute(text("SELECT month, type, is_savings_related, category_id, total FROM mv_monthly_totals"))
            .mappings()
            .fetchall()
        )
        return rows
    except Exception as exc:
        log.warning("mv_monthly_totals unavailable (%s) — falling back to ORM queries", exc)
        db.rollback()
        return None


def _mv_sum(
    rows,
    *,
    month: date | None = None,
    from_month: date | None = None,
    until_month: date | None = None,
    type_val: str | None = None,
    savings: bool | None = None,
    cat_ids: set[int] | None = None,
) -> float:
    """Sum `total` over MATVIEW rows matching the given filters."""
    total = 0.0
    for r in rows:
        m = r["month"]
        if month is not None and m != month:
            continue
        if from_month is not None and m < from_month:
            continue
        if until_month is not None and m > until_month:
            continue
        if type_val is not None and r["type"] != type_val:
            continue
        if savings is not None and bool(r["is_savings_related"]) != savings:
            continue
        if cat_ids is not None and r["category_id"] not in cat_ids:
            continue
        total += float(r["total"] or 0)
    return total


VALID_KPI_ROLES = ("liquid_savings", "real_estate")


def get_kpi_role_category_ids(db: Session) -> dict[str, list[int]]:
    """Return category IDs grouped by their KPI role.

    Keys are 'liquid_savings' and 'real_estate'; values are lists of expense
    category IDs. Only expense categories with a role are returned.
    """
    result: dict[str, list[int]] = {"liquid_savings": [], "real_estate": []}
    rows = (
        db.query(Category.id, Category.kpi_role)
        .filter(
            Category.kpi_role.isnot(None),
            Category.type == TransactionType.EXPENSE,
        )
        .all()
    )
    for cat_id, role in rows:
        if role in result:
            result[role].append(cat_id)
    return result


def _schedule_matview_refresh() -> None:
    """Fire-and-forget REFRESH MATERIALIZED VIEW CONCURRENTLY (PostgreSQL only).

    Spawns a daemon thread so the caller (ingest pipeline) is never blocked.
    CONCURRENTLY means existing readers are not locked out during the refresh.
    """
    if not _USE_MATVIEW:
        return

    def _refresh():
        from app.models.database import engine

        try:
            with engine.connect() as conn:
                conn.execute(text("REFRESH MATERIALIZED VIEW CONCURRENTLY mv_monthly_totals"))
                conn.commit()
        except Exception as exc:
            log.warning("MATVIEW refresh failed: %s", exc)

    threading.Thread(target=_refresh, daemon=True).start()


def invalidate_dashboard_cache(db: Session = None) -> None:
    """Invalidate the dashboard cache.

    Always clears the in-process in-memory dict. When *db* is supplied, also
    writes a sentinel row to period_rollups so that other pods (OCR worker,
    email worker) can trigger invalidation in the main app on the next request,
    and schedules an async MATVIEW refresh so the next dashboard load is fast.
    """
    with _cache_lock:
        _cache.clear()
    if db is not None:
        _db_write_sentinel(db)
        _schedule_matview_refresh()


def _cache_get(key: tuple, db: Session = None) -> Any | None:
    with _cache_lock:
        entry = _cache.get(key)
    if entry is None:
        return None
    mono_ts, wall_ts, value = entry
    if time.monotonic() - mono_ts > _CACHE_TTL:
        with _cache_lock:
            _cache.pop(key, None)
        return None
    # Cross-pod check: did a worker write a sentinel AFTER this pod cached the value?
    if db is not None:
        sentinel_ts = _db_get_sentinel_wall_ts(db)
        if sentinel_ts is not None and sentinel_ts > wall_ts:
            with _cache_lock:
                _cache.pop(key, None)
            return None
    return value


def _cache_set(key: tuple, value: Any) -> None:
    mono_ts = time.monotonic()
    wall_ts = datetime.now(timezone.utc).timestamp()
    with _cache_lock:
        _cache[key] = (mono_ts, wall_ts, value)


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


def get_cash_on_hand(db: Session) -> float:
    """All-time non-savings income minus all-time expense (soft-deleted excluded).

    The single figure the cash-flow forecast needs as its starting balance,
    computed with one aggregate query instead of the full dashboard pass. Mirrors
    the `total_income_all - total_expense_all` definition used in get_dashboard_data.
    """
    row = db.query(
        func.sum(
            case(
                (
                    and_(
                        Transaction.type == TransactionType.INCOME,
                        Transaction.is_savings_related == False,  # noqa: E712
                        Transaction.deleted_at.is_(None),
                    ),
                    Transaction.amount,
                ),
                else_=0,
            )
        ).label("inc"),
        func.sum(
            case(
                (
                    and_(
                        Transaction.type == TransactionType.EXPENSE,
                        Transaction.deleted_at.is_(None),
                    ),
                    Transaction.amount,
                ),
                else_=0,
            )
        ).label("exp"),
    ).first()
    return float(row.inc or 0) - float(row.exp or 0)


def get_dashboard_data(db: Session, year: int = None, month: int = None) -> dict:
    """Compute all dashboard metrics for the given year/month (defaults to today).

    Results are cached in-memory for _CACHE_TTL seconds, keyed by (year, month).
    Call invalidate_dashboard_cache() after any write that mutates dashboard data.
    """
    today = date.today()
    day = get_month_start_day(db)
    _cur_year, _cur_month = current_period_ym(today, day)
    current_year = year if year is not None else _cur_year
    current_month = month if month is not None else _cur_month

    _cache_key = (current_year, current_month)
    cached = _cache_get(_cache_key, db)
    if cached is not None:
        return cached
    month_start, month_end = fiscal_window_ym(current_year, current_month, day)

    from sqlalchemy import false as sqla_false, true as sqla_true

    # Resolve KPI bucket category IDs from explicit role assignments.
    kpi_ids = get_kpi_role_category_ids(db)
    ls_set = set(kpi_ids["liquid_savings"])
    re_set = set(kpi_ids["real_estate"])
    # Bucket categories are excluded from "living expense" (ordinary spending).
    bucket_set = ls_set | re_set

    # ── Aggregates ────────────────────────────────────────────────────────────
    # Fast path: read from mv_monthly_totals (pre-aggregated, refreshed async).
    # The matview groups by *calendar* month, so it's only valid when day == 1;
    # for custom fiscal windows (day != 1) we fall through to live ORM queries
    # over `transactions`, covered by ix_transactions_date_type_savings_category.
    # This is a deliberate perf trade-off: fiscal-cycle households pay a live
    # aggregation cost on every dashboard load instead of reading the
    # pre-aggregated matview. Acceptable at typical household transaction
    # volumes (low thousands of rows); revisit if this becomes measurably slow.
    # Fallback: inline ORM queries when the MATVIEW is unavailable (SQLite dev).
    use_matview = _USE_MATVIEW and day == 1
    mv_rows = _fetch_matview_rows(db) if use_matview else None

    if mv_rows is not None:
        monthly_income = _mv_sum(mv_rows, month=month_start, type_val="income", savings=False)
        # All non-savings expense (incl. bucket categories) — used for net cash.
        monthly_expense_full = _mv_sum(mv_rows, month=month_start, type_val="expense", savings=False)
        # Living expense for display/ratios excludes the KPI bucket categories.
        monthly_expense = (
            monthly_expense_full
            - _mv_sum(mv_rows, month=month_start, type_val="expense", savings=False, cat_ids=bucket_set)
            if bucket_set
            else monthly_expense_full
        )
        monthly_savings = _mv_sum(mv_rows, month=month_start, type_val="expense", savings=True)
        monthly_tiet_kiem = _mv_sum(mv_rows, month=month_start, type_val="expense", cat_ids=ls_set) if ls_set else 0.0
        monthly_bds = _mv_sum(mv_rows, month=month_start, type_val="expense", cat_ids=re_set) if re_set else 0.0
        total_income_all = _mv_sum(mv_rows, type_val="income", savings=False)
        total_expense_all = _mv_sum(mv_rows, type_val="expense")
    else:
        tk_filter = (
            Transaction.category_id.in_(kpi_ids["liquid_savings"]) if kpi_ids["liquid_savings"] else sqla_false()
        )
        bds_filter = Transaction.category_id.in_(kpi_ids["real_estate"]) if kpi_ids["real_estate"] else sqla_false()
        bucket_filter = ~Transaction.category_id.in_(bucket_set) if bucket_set else sqla_true()

        def _month_case(type_val, savings_val, extra=None):
            conds = [
                Transaction.type == type_val,
                Transaction.date >= month_start,
                Transaction.date <= month_end,
                Transaction.deleted_at.is_(None),
            ]
            if savings_val is not None:
                conds.append(Transaction.is_savings_related == savings_val)
            if extra is not None:
                conds.append(extra)
            return func.sum(case((and_(*conds), Transaction.amount), else_=0))

        def _alltime_case(type_val, savings_val=None):
            conds = [Transaction.type == type_val, Transaction.deleted_at.is_(None)]
            if savings_val is not None:
                conds.append(Transaction.is_savings_related == savings_val)
            return func.sum(case((and_(*conds), Transaction.amount), else_=0))

        _agg = db.query(
            _month_case(TransactionType.INCOME, False).label("monthly_income"),
            _month_case(TransactionType.EXPENSE, False, bucket_filter).label("monthly_expense"),
            _month_case(TransactionType.EXPENSE, False).label("monthly_expense_full"),
            _month_case(TransactionType.EXPENSE, True).label("monthly_savings"),
            _month_case(TransactionType.EXPENSE, None, tk_filter).label("monthly_tiet_kiem"),
            _month_case(TransactionType.EXPENSE, None, bds_filter).label("monthly_bds"),
            _alltime_case(TransactionType.INCOME, False).label("total_income"),
            _alltime_case(TransactionType.EXPENSE).label("total_expense"),
        ).first()
        monthly_income = float(_agg.monthly_income or 0)
        monthly_expense = float(_agg.monthly_expense or 0)
        monthly_expense_full = float(_agg.monthly_expense_full or 0)
        monthly_savings = float(_agg.monthly_savings or 0)
        monthly_tiet_kiem = float(_agg.monthly_tiet_kiem or 0)
        monthly_bds = float(_agg.monthly_bds or 0)
        total_income_all = float(_agg.total_income or 0)
        total_expense_all = float(_agg.total_expense or 0)

    monthly_wealth_building = monthly_tiet_kiem + monthly_bds

    savings_rate = round(monthly_wealth_building / monthly_income * 100, 1) if monthly_income > 0 else 0
    liquid_savings_rate = round(monthly_tiet_kiem / monthly_income * 100, 1) if monthly_income > 0 else 0
    bds_rate = round(monthly_bds / monthly_income * 100, 1) if monthly_income > 0 else 0
    living_expense_ratio = round(monthly_expense / monthly_income * 100, 1) if monthly_income > 0 else 0

    # ── Prev-month aggregates (delta arrows) ─────────────────────────────────
    prev_year_num, prev_month_num = shift_period_ym(current_year, current_month, -1)
    prev_month_start, prev_month_end = fiscal_window_ym(prev_year_num, prev_month_num, day)

    if mv_rows is not None:
        _pi = _mv_sum(mv_rows, month=prev_month_start, type_val="income", savings=False)
        # Full non-savings expense (incl. buckets) for net cash; bucket-excluded for the ratio.
        _pe_full = _mv_sum(mv_rows, month=prev_month_start, type_val="expense", savings=False)
        _pe = (
            _pe_full - _mv_sum(mv_rows, month=prev_month_start, type_val="expense", savings=False, cat_ids=bucket_set)
            if bucket_set
            else _pe_full
        )
        _ps = _mv_sum(mv_rows, month=prev_month_start, type_val="expense", savings=True)
        _prev_tiet_kiem = (
            _mv_sum(mv_rows, month=prev_month_start, type_val="expense", cat_ids=ls_set) if ls_set else 0.0
        )
        _prev_bds = _mv_sum(mv_rows, month=prev_month_start, type_val="expense", cat_ids=re_set) if re_set else 0.0
    else:
        tk_filter = (
            Transaction.category_id.in_(kpi_ids["liquid_savings"]) if kpi_ids["liquid_savings"] else sqla_false()
        )
        bds_filter = Transaction.category_id.in_(kpi_ids["real_estate"]) if kpi_ids["real_estate"] else sqla_false()
        bucket_filter = ~Transaction.category_id.in_(bucket_set) if bucket_set else sqla_true()

        def _prev_case(type_val, savings_val, extra=None):
            conds = [
                Transaction.type == type_val,
                Transaction.date >= prev_month_start,
                Transaction.date <= prev_month_end,
                Transaction.deleted_at.is_(None),
            ]
            if savings_val is not None:
                conds.append(Transaction.is_savings_related == savings_val)
            if extra is not None:
                conds.append(extra)
            return func.sum(case((and_(*conds), Transaction.amount), else_=0))

        _prev = db.query(
            _prev_case(TransactionType.INCOME, False).label("income"),
            _prev_case(TransactionType.EXPENSE, False, bucket_filter).label("expense"),
            _prev_case(TransactionType.EXPENSE, False).label("expense_full"),
            _prev_case(TransactionType.EXPENSE, True).label("savings"),
            _prev_case(TransactionType.EXPENSE, None, tk_filter).label("tiet_kiem"),
            _prev_case(TransactionType.EXPENSE, None, bds_filter).label("bds"),
        ).first()
        _pi = float(_prev.income or 0)
        _pe = float(_prev.expense or 0)
        _pe_full = float(_prev.expense_full or 0)
        _ps = float(_prev.savings or 0)
        _prev_tiet_kiem = float(_prev.tiet_kiem or 0)
        _prev_bds = float(_prev.bds or 0)

    prev_liquid_savings_rate = round(_prev_tiet_kiem / _pi * 100, 1) if _pi > 0 else 0
    prev_bds_rate = round(_prev_bds / _pi * 100, 1) if _pi > 0 else 0
    prev_net_cash = _pi - _pe_full - _ps
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
    # Avg living expense over last 3 completed periods (not counting current)
    _ef_year, _ef_month = shift_period_ym(prev_year_num, prev_month_num, -2)
    _ef_start, _ = fiscal_window_ym(_ef_year, _ef_month, day)
    _ef_end = prev_month_end
    if mv_rows is not None:
        _ef_total = _mv_sum(
            mv_rows,
            from_month=_ef_start,
            until_month=prev_month_start,
            type_val="expense",
            savings=False,
        )
    else:
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

    cash_on_hand = total_income_all - total_expense_all

    _assets_agg = db.query(
        func.coalesce(func.sum(OtherAsset.current_value_vnd), 0).label("cur"),
        func.coalesce(func.sum(OtherAsset.purchase_price_vnd), 0).label("pur"),
        func.count(OtherAsset.id).label("cnt"),
    ).first()
    total_assets_current = float(_assets_agg.cur)
    total_assets_purchase = float(_assets_agg.pur)
    _assets_count = int(_assets_agg.cnt)

    total_projects_paid = float(
        db.query(func.sum(ProjectPayment.amount)).filter(ProjectPayment.status == PaymentStatus.PAID).scalar() or 0
    )

    net_worth = cash_on_hand + total_savings + total_assets_current + total_projects_paid

    # ── Budget adherence ──────────────────────────────────────────────────────
    # Use the resolved fiscal period (not the raw calendar month) so these counts
    # match the income/expense KPIs above and the Budget page when day != 1.
    today_ym = f"{current_year:04d}-{current_month:02d}"
    try:
        budget_rows = compute_budget_rows(db, today_ym, day)
    except Exception:
        log.exception("compute_budget_rows failed for %s — rolling back and continuing", today_ym)
        db.rollback()
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
    bds_monthly_installment = float((bds_next_payment.amount if bds_next_payment else monthly_bds) or 0)
    stress_test_required = avg_monthly_expense + 20_000_000 + bds_monthly_installment
    stress_test_cushion = monthly_income - stress_test_required

    deadline_cutoff = today + timedelta(days=180)
    at_risk_ids = {
        p.id
        for p in active_projects_list
        if p.deadline
        and p.deadline <= deadline_cutoff
        and p.target_amount > 0
        and (p.current_amount or 0) / p.target_amount < 0.5
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
    if mv_rows is not None:
        all_cats_dict = {c.id: c for c in db.query(Category).all()}
        cat_totals: dict[int, float] = {}
        for r in mv_rows:
            if (
                r["month"] == month_start
                and r["type"] == "expense"
                and not r["is_savings_related"]
                and r["category_id"] != 0  # 0 = COALESCE sentinel for NULL
            ):
                cid = r["category_id"]
                cat_totals[cid] = cat_totals.get(cid, 0.0) + float(r["total"] or 0)
        cat_rows_sorted = sorted(cat_totals.items(), key=lambda x: -x[1])
        expense_by_category = [
            {
                "name": all_cats_dict[cid].name,
                "total": total,
                "color": all_cats_dict[cid].color,
                "percentage": total / monthly_expense * 100 if monthly_expense > 0 else 0,
            }
            for cid, total in cat_rows_sorted
            if cid in all_cats_dict and total > 0
        ]
    else:
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

    # Net worth 1 month ago: cumulative income − expense up to end of prev month
    if mv_rows is not None:
        _prev_cum_income = _mv_sum(mv_rows, until_month=prev_month_start, type_val="income", savings=False)
        _prev_cum_expense = _mv_sum(mv_rows, until_month=prev_month_start, type_val="expense")
    else:
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
    _prev_assets_total = total_assets_current
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
    if passive_cat_ids:
        if mv_rows is not None:
            passive_income_monthly = _mv_sum(
                mv_rows,
                month=month_start,
                type_val="income",
                cat_ids=set(passive_cat_ids),
            )
        else:
            passive_income_monthly = float(
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
    else:
        passive_income_monthly = 0.0
    passive_income_pct = round(passive_income_monthly / monthly_income * 100, 1) if monthly_income > 0 else 0.0

    # Baby fund bundle
    _bf_raw = get_setting(db, "baby_fund_bundle_id")
    baby_fund_bundle = None
    if _bf_raw:
        baby_fund_bundle = (
            db.query(SavingsBundle).filter(SavingsBundle.id == int(_bf_raw), SavingsBundle.deleted_at.is_(None)).first()
        )

    # ── Family Safety Score checks (shared by initial render and HTMX refresh) ──
    # Net cash uses full non-savings expense (incl. KPI bucket categories), not the
    # bucket-excluded living-expense figure, so bucket spend isn't double-counted away.
    _net_this_month = monthly_income - monthly_expense_full - monthly_savings
    check_income = monthly_income > 0
    check_bds = monthly_bds > 0
    check_tk = liquid_savings_rate >= savings_target_pct
    check_net = _net_this_month > 0
    ss_score = sum([check_income, check_bds, check_tk, check_net])

    from app.services.forecast_service import build_forecast

    _f = build_forecast(db, horizon_days=30)
    outlook = {"low_point": _f["low_point"], "horizon_net": _f["horizon_net"], "shortfall": _f["shortfall"]}

    result = {
        "outlook": outlook,
        "check_income": check_income,
        "check_bds": check_bds,
        "check_tk": check_tk,
        "check_net": check_net,
        "ss_score": ss_score,
        "summary": {
            "total_income": monthly_income,
            "total_expense": monthly_expense,
            "total_savings_expense": monthly_savings,
            "net_this_month": _net_this_month,
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
            "total_assets_count": _assets_count,
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
        "period_label": f"{current_year:04d}-{current_month:02d}",
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
