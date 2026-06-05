"""Tests for dashboard KPI calculations.

These test the core financial logic directly against the DB — the most
regression-sensitive code in the codebase.
"""

from datetime import date
import pytest

from app.models.database import (
    SavingsBundle,
    OtherAsset,
    TransactionType,
    SavingsStatus,
    SavingsType,
)
from app.routers.dashboard import get_dashboard_page_data
from tests.conftest import make_transaction

THIS_YEAR = 2026
THIS_MONTH = 4  # April — matches the test data we reason about


# ── Helpers ───────────────────────────────────────────────────────────────────


def _summary(db, year=THIS_YEAR, month=THIS_MONTH):
    return get_dashboard_page_data(db, year=year, month=month)["summary"]


# ── Savings Rate ──────────────────────────────────────────────────────────────


def test_savings_rate_zero_when_no_income(db_session, tiet_kiem_cat):
    """No income → savings rate must be 0 (avoid division by zero)."""
    make_transaction(
        db_session,
        date_val=date(THIS_YEAR, THIS_MONTH, 1),
        amount=10_000_000,
        type_=TransactionType.EXPENSE,
        category_id=tiet_kiem_cat.id,
    )
    s = _summary(db_session)
    assert s["savings_rate"] == 0


def test_savings_rate_uses_tiet_kiem_and_bds(db_session, income_cat, tiet_kiem_cat, bds_cat):
    """Savings rate = (Tiết kiệm + Bất động sản) / income × 100."""
    make_transaction(
        db_session,
        date_val=date(THIS_YEAR, THIS_MONTH, 1),
        amount=100_000_000,
        type_=TransactionType.INCOME,
        category_id=income_cat.id,
    )
    make_transaction(
        db_session,
        date_val=date(THIS_YEAR, THIS_MONTH, 2),
        amount=50_000_000,
        type_=TransactionType.EXPENSE,
        category_id=tiet_kiem_cat.id,
    )
    make_transaction(
        db_session,
        date_val=date(THIS_YEAR, THIS_MONTH, 3),
        amount=30_000_000,
        type_=TransactionType.EXPENSE,
        category_id=bds_cat.id,
    )

    s = _summary(db_session)
    assert s["savings_rate"] == pytest.approx(80.0, rel=1e-2)


def test_savings_rate_only_tiet_kiem_no_bds(db_session, income_cat, tiet_kiem_cat):
    """Works correctly when only Tiết kiệm has transactions."""
    make_transaction(
        db_session,
        date_val=date(THIS_YEAR, THIS_MONTH, 1),
        amount=80_000_000,
        type_=TransactionType.INCOME,
        category_id=income_cat.id,
    )
    make_transaction(
        db_session,
        date_val=date(THIS_YEAR, THIS_MONTH, 2),
        amount=20_000_000,
        type_=TransactionType.EXPENSE,
        category_id=tiet_kiem_cat.id,
    )

    s = _summary(db_session)
    assert s["savings_rate"] == pytest.approx(25.0, rel=1e-2)


def test_savings_rate_excludes_other_expense_categories(db_session, income_cat, expense_cat, tiet_kiem_cat):
    """Regular expenses must NOT count toward savings rate."""
    make_transaction(
        db_session,
        date_val=date(THIS_YEAR, THIS_MONTH, 1),
        amount=100_000_000,
        type_=TransactionType.INCOME,
        category_id=income_cat.id,
    )
    make_transaction(
        db_session,
        date_val=date(THIS_YEAR, THIS_MONTH, 2),
        amount=40_000_000,
        type_=TransactionType.EXPENSE,
        category_id=expense_cat.id,
    )  # Food — should not count
    make_transaction(
        db_session,
        date_val=date(THIS_YEAR, THIS_MONTH, 3),
        amount=20_000_000,
        type_=TransactionType.EXPENSE,
        category_id=tiet_kiem_cat.id,
    )  # Tiết kiệm — counts

    s = _summary(db_session)
    assert s["savings_rate"] == pytest.approx(20.0, rel=1e-2)


def test_savings_rate_scoped_to_selected_month(db_session, income_cat, tiet_kiem_cat):
    """Transactions in a different month must not affect the selected month's rate."""
    make_transaction(
        db_session,
        date_val=date(THIS_YEAR, THIS_MONTH, 1),
        amount=100_000_000,
        type_=TransactionType.INCOME,
        category_id=income_cat.id,
    )
    # Tiết kiệm in previous month — should not count for THIS_MONTH
    make_transaction(
        db_session,
        date_val=date(THIS_YEAR, THIS_MONTH - 1, 15),
        amount=50_000_000,
        type_=TransactionType.EXPENSE,
        category_id=tiet_kiem_cat.id,
    )

    s = _summary(db_session)
    assert s["savings_rate"] == 0.0


# ── Critical checks ───────────────────────────────────────────────────────────


def test_monthly_tiet_kiem_amount(db_session, income_cat, tiet_kiem_cat):
    """monthly_tiet_kiem reflects exact amount deposited this month."""
    make_transaction(
        db_session,
        date_val=date(THIS_YEAR, THIS_MONTH, 1),
        amount=100_000_000,
        type_=TransactionType.INCOME,
        category_id=income_cat.id,
    )
    make_transaction(
        db_session,
        date_val=date(THIS_YEAR, THIS_MONTH, 5),
        amount=25_000_000,
        type_=TransactionType.EXPENSE,
        category_id=tiet_kiem_cat.id,
    )

    s = _summary(db_session)
    assert s["monthly_tiet_kiem"] == pytest.approx(25_000_000)


def test_monthly_bds_amount(db_session, income_cat, bds_cat):
    """monthly_bds reflects exact amount paid to real-estate project this month."""
    make_transaction(
        db_session,
        date_val=date(THIS_YEAR, THIS_MONTH, 1),
        amount=100_000_000,
        type_=TransactionType.INCOME,
        category_id=income_cat.id,
    )
    make_transaction(
        db_session,
        date_val=date(THIS_YEAR, THIS_MONTH, 10),
        amount=30_000_000,
        type_=TransactionType.EXPENSE,
        category_id=bds_cat.id,
    )

    s = _summary(db_session)
    assert s["monthly_bds"] == pytest.approx(30_000_000)


def test_monthly_tiet_kiem_zero_when_no_transaction(db_session):
    """monthly_tiet_kiem is 0 when no Tiết kiệm category exists or has transactions."""
    s = _summary(db_session)
    assert s["monthly_tiet_kiem"] == 0


def test_monthly_bds_zero_when_no_transaction(db_session):
    s = _summary(db_session)
    assert s["monthly_bds"] == 0


# ── Net worth ──────────────────────────────────────────────────────────────────


def test_net_worth_components(db_session, income_cat, expense_cat):
    """Net worth = cash_on_hand + active savings (future) + other assets + projects paid."""
    # Cash on hand: all-time income minus all-time expenses
    make_transaction(
        db_session,
        date_val=date(2026, 1, 1),
        amount=100_000_000,
        type_=TransactionType.INCOME,
        category_id=income_cat.id,
    )
    make_transaction(
        db_session,
        date_val=date(2026, 1, 2),
        amount=20_000_000,
        type_=TransactionType.EXPENSE,
        category_id=expense_cat.id,
    )
    # cash_on_hand = 80_000_000

    # Active savings bundle (future_amount counts)
    bundle = SavingsBundle(
        name="Test Bundle",
        bank_name="VCB",
        type=SavingsType.FIXED_DEPOSIT,
        initial_deposit=50_000_000,
        current_amount=50_000_000,
        future_amount=53_000_000,
        interest_rate=6.0,
        start_date=date(2026, 1, 1),
        status=SavingsStatus.ACTIVE,
    )
    db_session.add(bundle)

    # Other asset
    asset = OtherAsset(
        name="Gold",
        asset_type="gold",
        quantity=1.0,
        unit="taels",
        purchase_price_vnd=8_000_000,
        current_value_vnd=9_000_000,
    )
    db_session.add(asset)
    db_session.commit()

    s = _summary(db_session)
    expected = 80_000_000 + 53_000_000 + 9_000_000
    assert s["net_worth"] == pytest.approx(expected)
    assert s["cash_on_hand"] == pytest.approx(80_000_000)
    assert s["total_savings"] == pytest.approx(53_000_000)
    assert s["total_assets_current"] == pytest.approx(9_000_000)


def test_net_worth_empty_db(db_session):
    """Net worth is 0 on an empty database."""
    s = _summary(db_session)
    assert s["net_worth"] == 0
    assert s["cash_on_hand"] == 0


# ── Monthly income / expense / savings ────────────────────────────────────────


def test_monthly_expense_excludes_savings_related(db_session, income_cat, expense_cat, tiet_kiem_cat):
    """total_expense excludes is_savings_related=True transactions."""
    make_transaction(
        db_session,
        date_val=date(THIS_YEAR, THIS_MONTH, 1),
        amount=100_000_000,
        type_=TransactionType.INCOME,
        category_id=income_cat.id,
    )
    make_transaction(
        db_session,
        date_val=date(THIS_YEAR, THIS_MONTH, 2),
        amount=30_000_000,
        type_=TransactionType.EXPENSE,
        category_id=expense_cat.id,
        is_savings_related=False,
    )
    make_transaction(
        db_session,
        date_val=date(THIS_YEAR, THIS_MONTH, 3),
        amount=20_000_000,
        type_=TransactionType.EXPENSE,
        category_id=tiet_kiem_cat.id,
        is_savings_related=True,
    )

    s = _summary(db_session)
    assert s["total_expense"] == pytest.approx(30_000_000)
    assert s["total_savings_expense"] == pytest.approx(20_000_000)


def test_net_this_month(db_session, income_cat, expense_cat, tiet_kiem_cat):
    """net_this_month = income − living_expense − savings_expense."""
    make_transaction(
        db_session,
        date_val=date(THIS_YEAR, THIS_MONTH, 1),
        amount=100_000_000,
        type_=TransactionType.INCOME,
        category_id=income_cat.id,
    )
    make_transaction(
        db_session,
        date_val=date(THIS_YEAR, THIS_MONTH, 2),
        amount=30_000_000,
        type_=TransactionType.EXPENSE,
        category_id=expense_cat.id,
    )
    make_transaction(
        db_session,
        date_val=date(THIS_YEAR, THIS_MONTH, 3),
        amount=50_000_000,
        type_=TransactionType.EXPENSE,
        category_id=tiet_kiem_cat.id,
        is_savings_related=True,
    )

    s = _summary(db_session)
    assert s["net_this_month"] == pytest.approx(20_000_000)


def test_dashboard_cache_hit_returns_same_object(db_session, income_cat, expense_cat):
    """Second call to get_dashboard_page_data returns the cached dict."""
    from app.routers.dashboard import get_dashboard_page_data
    from app.services.dashboard_service import invalidate_dashboard_cache

    invalidate_dashboard_cache()
    first = get_dashboard_page_data(db_session)
    second = get_dashboard_page_data(db_session)
    assert first is second


def test_dashboard_cache_miss_after_invalidation(db_session, income_cat, expense_cat):
    """Invalidating the cache causes a fresh computation."""
    from app.routers.dashboard import get_dashboard_page_data
    from app.services.dashboard_service import invalidate_dashboard_cache

    invalidate_dashboard_cache()
    first = get_dashboard_page_data(db_session)
    invalidate_dashboard_cache()
    second = get_dashboard_page_data(db_session)
    # Different objects but same structure
    assert first is not second
    assert "summary" in second


def test_dashboard_cache_expired_returns_fresh_data(db_session, income_cat, expense_cat):
    """Expired cache entries are evicted and fresh data is returned."""
    import time
    from app.services.dashboard_service import _cache, _cache_lock, _cache_get

    key = (2026, 1)
    sentinel = {"summary": {"total_income": 999}}

    # Plant an already-expired entry (format: mono_ts, wall_ts, value)
    with _cache_lock:
        _cache[key] = (time.monotonic() - 9999, time.time() - 9999, sentinel)

    result = _cache_get(key)
    assert result is None
    with _cache_lock:
        assert key not in _cache


def test_ns_helpers_cover_all_branches():
    """SimpleNamespace helpers must produce attribute-accessible objects for Jinja2 templates."""
    from types import SimpleNamespace
    from app.services.dashboard_service import _project_ns, _savings_ns, _payment_ns, _txn_ns
    from app.models.database import TransactionType

    proj = SimpleNamespace(
        id=1, name="Apt", current_amount=1_000_000, target_amount=5_000_000, deadline=date(2026, 12, 31)
    )
    ns = _project_ns(proj)
    assert ns.id == 1 and ns.name == "Apt" and ns.deadline == date(2026, 12, 31)

    sav = SimpleNamespace(
        name="Bundle",
        bank_name="VCB",
        maturity_date=date(2026, 6, 1),
        future_amount=10_000_000,
        current_amount=9_000_000,
    )
    ns = _savings_ns(sav)
    assert ns.bank_name == "VCB" and ns.future_amount == 10_000_000

    pmt = SimpleNamespace(amount=5_000_000, due_date=date(2026, 7, 1))
    ns = _payment_ns(pmt)
    assert ns.amount == 5_000_000

    cat = SimpleNamespace(name="Food")
    txn = SimpleNamespace(
        description="Lunch",
        date=date(2026, 5, 1),
        type=TransactionType.EXPENSE,
        amount=100_000,
        category=cat,
    )
    ns = _txn_ns(txn)
    assert ns.category.name == "Food"

    txn_no_cat = SimpleNamespace(
        description="X",
        date=date(2026, 5, 1),
        type=TransactionType.EXPENSE,
        amount=0,
        category=None,
    )
    ns2 = _txn_ns(txn_no_cat)
    assert ns2.category.name == ""


def test_sentinel_update_branch_and_cache_eviction(db_session):
    """Cover: sentinel update path, cross-pod eviction in _cache_get, and exception branches."""
    import time
    from app.services.dashboard_service import (
        _cache,
        _cache_lock,
        _cache_get,
        _cache_set,
        _db_get_sentinel_wall_ts,
        invalidate_dashboard_cache,
    )

    # Write sentinel (insert path)
    invalidate_dashboard_cache(db=db_session)
    ts1 = _db_get_sentinel_wall_ts(db_session)
    assert ts1 is not None

    # Write again — exercises the update (row.computed_at = now) branch
    import time as _time

    _time.sleep(0.01)
    invalidate_dashboard_cache(db=db_session)
    ts2 = _db_get_sentinel_wall_ts(db_session)
    assert ts2 >= ts1

    # Plant a cache entry with a wall_ts OLDER than the sentinel
    key = (9999, 12)
    with _cache_lock:
        _cache[key] = (time.monotonic(), ts1 - 1, {"sentinel": "old"})

    # _cache_get should evict because sentinel_ts > wall_ts
    result = _cache_get(key, db=db_session)
    assert result is None
    with _cache_lock:
        assert key not in _cache

    # Plant a fresh cache entry (wall_ts AFTER sentinel) — should be returned
    _cache_set(key, {"sentinel": "fresh"})
    result = _cache_get(key, db=db_session)
    assert result == {"sentinel": "fresh"}
