"""Integration tests for configurable fiscal-period KPI windows.

Proves that a non-1 start day shifts the windows used by the budget tracker
and the dashboard, and that day=1 stays byte-for-byte identical to the
pre-feature behaviour.

These tests run on both SQLite (default) and PostgreSQL (make test-pg). The
PG run exercises the materialized-view path for day=1 and the gated ORM
fallback for day!=1 — both must produce the same window semantics.
"""

from datetime import date

import pytest

from app.models.database import (
    BudgetAllocation,
    Category,
    TransactionType,
)
from app.services.budget_service import compute_budget_rows
from app.services.dashboard_service import (
    get_dashboard_data,
    invalidate_dashboard_cache,
)
from app.services.fiscal_period import (
    SETTING_KEY,
    fiscal_window_ym,
    get_month_start_day,
)
from app.services.settings_service import set_setting
from tests.conftest import make_transaction


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def expense_cat(db_session):
    cat = Category(name="Food", type=TransactionType.EXPENSE, color="#EF4444", icon="utensils")
    db_session.add(cat)
    db_session.commit()
    db_session.refresh(cat)
    return cat


@pytest.fixture()
def income_cat(db_session):
    cat = Category(name="Salary", type=TransactionType.INCOME, color="#10B981", icon="money")
    db_session.add(cat)
    db_session.commit()
    db_session.refresh(cat)
    return cat


# ── Budget window integration ────────────────────────────────────────────────
# With day=1 the period labeled "2026-06" is the calendar month 2026-06-01..30.
# With day=19 the same label becomes 2026-06-19..2026-07-18. An expense on
# June 17 falls in the previous period (May's) under day=19 but in "2026-06"
# under day=1. An expense on June 20 falls in "2026-06" under day=19.


def _add_budget_alloc(db, category_id, year_month, amount):
    a = BudgetAllocation(category_id=category_id, year_month=year_month, amount=amount)
    db.add(a)
    db.commit()
    return a


def test_budget_windows_day1_regression(db_session, expense_cat):
    """day=1: both the 17th and the 20th of the labeled month count as this_month."""
    _add_budget_alloc(db_session, expense_cat.id, "2026-06", 5_000_000)
    make_transaction(
        db_session,
        date_val=date(2026, 6, 17),
        amount=1_000_000,
        type_=TransactionType.EXPENSE,
        category_id=expense_cat.id,
    )
    make_transaction(
        db_session,
        date_val=date(2026, 6, 20),
        amount=2_000_000,
        type_=TransactionType.EXPENSE,
        category_id=expense_cat.id,
    )

    rows = compute_budget_rows(db_session, "2026-06")
    assert len(rows) == 1
    assert rows[0]["this_month_spent"] == pytest.approx(3_000_000)


def test_budget_windows_day19_shifts_window(db_session, expense_cat):
    """day=19: the period labeled '2026-06' runs 06-19..07-18.
    The 20th is in this period; the 17th is in the previous one.
    """
    set_setting(db_session, SETTING_KEY, "19")
    assert get_month_start_day(db_session) == 19

    # Window sanity
    assert fiscal_window_ym(2026, 6, 19) == (date(2026, 6, 19), date(2026, 7, 18))

    _add_budget_alloc(db_session, expense_cat.id, "2026-06", 5_000_000)
    make_transaction(
        db_session,
        date_val=date(2026, 6, 17),
        amount=1_000_000,
        type_=TransactionType.EXPENSE,
        category_id=expense_cat.id,
    )
    make_transaction(
        db_session,
        date_val=date(2026, 6, 20),
        amount=2_000_000,
        type_=TransactionType.EXPENSE,
        category_id=expense_cat.id,
    )

    rows = compute_budget_rows(db_session, "2026-06")
    assert len(rows) == 1
    # Only the 20th is inside the 06-19..07-18 window.
    assert rows[0]["this_month_spent"] == pytest.approx(2_000_000)


# ── Dashboard window integration ─────────────────────────────────────────────
# Seed two expenses straddling the 19th within the same calendar month plus an
# income for the month. Verify monthly_expense (and total_income) shift when
# the window boundary moves.


def test_dashboard_day1_regression(db_session, income_cat, expense_cat):
    """day=1: both expenses in the calendar month count."""
    make_transaction(
        db_session,
        date_val=date(2026, 6, 1),
        amount=30_000_000,
        type_=TransactionType.INCOME,
        category_id=income_cat.id,
    )
    make_transaction(
        db_session,
        date_val=date(2026, 6, 17),
        amount=1_000_000,
        type_=TransactionType.EXPENSE,
        category_id=expense_cat.id,
    )
    make_transaction(
        db_session,
        date_val=date(2026, 6, 20),
        amount=2_000_000,
        type_=TransactionType.EXPENSE,
        category_id=expense_cat.id,
    )

    data = get_dashboard_data(db_session, year=2026, month=6)
    s = data["summary"]
    assert s["total_income"] == pytest.approx(30_000_000)
    assert s["total_expense"] == pytest.approx(3_000_000)


def test_dashboard_day19_shifts_window(db_session, income_cat, expense_cat):
    """day=19: only the post-19th expense is inside the 06-19..07-18 window."""
    set_setting(db_session, SETTING_KEY, "19")
    assert get_month_start_day(db_session) == 19

    make_transaction(
        db_session,
        date_val=date(2026, 6, 1),
        amount=30_000_000,
        type_=TransactionType.INCOME,
        category_id=income_cat.id,
    )
    make_transaction(
        db_session,
        date_val=date(2026, 6, 17),
        amount=1_000_000,
        type_=TransactionType.EXPENSE,
        category_id=expense_cat.id,
    )
    make_transaction(
        db_session,
        date_val=date(2026, 6, 20),
        amount=2_000_000,
        type_=TransactionType.EXPENSE,
        category_id=expense_cat.id,
    )

    # 06-19..07-18 window: income on 06-01 is OUT of window (belongs to prev),
    # the 17th expense is OUT, the 20th expense is IN.
    invalidate_dashboard_cache(db_session)
    data = get_dashboard_data(db_session, year=2026, month=6)
    s = data["summary"]
    assert s["total_income"] == pytest.approx(0.0)
    assert s["total_expense"] == pytest.approx(2_000_000)


def test_dashboard_matview_gate_on_sqlite(db_session, income_cat, expense_cat):
    """Explicit gate assertion: _USE_MATVIEW is False on SQLite, so the ORM
    path runs for any day value. The day=19 window still computes correctly
    via the ORM fallback.
    """
    from app.services.dashboard_service import _USE_MATVIEW
    import os

    is_pg = os.getenv("TEST_DATABASE_URL", "").startswith("postgresql")
    if is_pg:
        pytest.skip("matview-gate assertion is SQLite-specific")

    assert _USE_MATVIEW is False

    set_setting(db_session, SETTING_KEY, "19")
    make_transaction(
        db_session,
        date_val=date(2026, 6, 20),
        amount=2_000_000,
        type_=TransactionType.EXPENSE,
        category_id=expense_cat.id,
    )
    invalidate_dashboard_cache(db_session)
    data = get_dashboard_data(db_session, year=2026, month=6)
    # ORM path runs (no matview) — still returns the correct window totals.
    assert data["summary"]["total_expense"] == pytest.approx(2_000_000)


# ── Settings round-trip: POST /settings/pay-cycle → dashboard reflects it ────


def test_pay_cycle_settings_round_trip(client, db_session, income_cat, expense_cat):
    """Posting to /settings/pay-cycle updates the dashboard window and invalidates the cache."""
    # Seed income+expense inside the calendar month, both after the 19th so
    # the day=19 window picks them up.
    make_transaction(
        db_session,
        date_val=date(2026, 6, 20),
        amount=30_000_000,
        type_=TransactionType.INCOME,
        category_id=income_cat.id,
    )
    make_transaction(
        db_session,
        date_val=date(2026, 6, 21),
        amount=4_000_000,
        type_=TransactionType.EXPENSE,
        category_id=expense_cat.id,
    )

    # Pre-state: day=1 → dashboard sees both txns (calendar month window).
    invalidate_dashboard_cache(db_session)
    before = get_dashboard_data(db_session, year=2026, month=6)
    assert before["summary"]["total_expense"] == pytest.approx(4_000_000)

    # POST to /settings/pay-cycle → writes setting + invalidates cache.
    r = client.post("/settings/pay-cycle", data={"month_start_day": "19"})
    assert r.status_code == 200

    # Cache was invalidated: a fresh call picks up the new day=19 window.
    # Both txns (20th + 21st) are still inside 06-19..07-18.
    after = get_dashboard_data(db_session, year=2026, month=6)
    assert after is not before  # different object → cache miss, recomputed
    assert after["summary"]["total_expense"] == pytest.approx(4_000_000)
    assert after["summary"]["total_income"] == pytest.approx(30_000_000)

    # Seed a txn BEFORE the 19th → excluded from the new window.
    make_transaction(
        db_session,
        date_val=date(2026, 6, 10),
        amount=5_000_000,
        type_=TransactionType.EXPENSE,
        category_id=expense_cat.id,
    )
    invalidate_dashboard_cache(db_session)
    shifted = get_dashboard_data(db_session, year=2026, month=6)
    # Only the 21st expense is in 06-19..07-18.
    assert shifted["summary"]["total_expense"] == pytest.approx(4_000_000)


def test_dashboard_budget_adherence_follows_requested_period(db_session, expense_cat):
    """The budget-adherence block must use the resolved fiscal period, not the
    real calendar 'today'. Seeding a 2026-06 allocation and requesting that
    period must surface it regardless of when the test runs.
    """
    _add_budget_alloc(db_session, expense_cat.id, "2026-06", 5_000_000)
    make_transaction(
        db_session,
        date_val=date(2026, 6, 20),
        amount=2_000_000,
        type_=TransactionType.EXPENSE,
        category_id=expense_cat.id,
    )

    invalidate_dashboard_cache(db_session)
    data = get_dashboard_data(db_session, year=2026, month=6)
    # Under the old code this read the real `today` month and would be 0 on any
    # day outside June 2026; now it tracks the requested period.
    assert data["summary"]["budget_total"] == 1


def test_transactions_page_injects_default_month_start_day(client):
    """The transactions page exposes the pay-cycle day to its JS so the
    category-budget audit filter spans the same fiscal window as the server."""
    import re

    r = client.get("/transactions")
    assert r.status_code == 200
    m = re.search(r"CARANGE_MONTH_START_DAY = (\d+)", r.text)
    assert m and m.group(1) == "1"
    assert "_fiscalWindow" in r.text


def test_transactions_page_injects_configured_month_start_day(client, db_session):
    set_setting(db_session, SETTING_KEY, "19")
    r = client.get("/transactions")
    assert r.status_code == 200
    import re

    m = re.search(r"CARANGE_MONTH_START_DAY = (\d+)", r.text)
    assert m and m.group(1) == "19"


def test_dashboard_budget_snapshot_shows_remaining_and_period_link(client, db_session, expense_cat):
    """The Budget Snapshot link targets the fiscal-period label (not raw
    calendar today), and the text shows remaining money, not spent/total."""
    today = date.today()
    label = f"{today.year:04d}-{today.month:02d}"  # default day=1 → calendar month
    _add_budget_alloc(db_session, expense_cat.id, label, 5_000_000)
    make_transaction(
        db_session,
        date_val=today,
        amount=2_000_000,
        type_=TransactionType.EXPENSE,
        category_id=expense_cat.id,
    )
    invalidate_dashboard_cache(db_session)

    r = client.get("/")
    assert r.status_code == 200
    assert "today.strftime" not in r.text
    # Link carries the fiscal-period label
    assert f"/transactions?category_id={expense_cat.id}&month={label}" in r.text
    # Remaining money (5M - 2M = 3M) is shown as "... left", not "2,000,000 / ..."
    assert "left" in r.text
    assert "2,000,000 / " not in r.text
