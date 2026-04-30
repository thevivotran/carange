"""Tests for dashboard KPI calculations.

These test the core financial logic directly against the DB — the most
regression-sensitive code in the codebase.
"""
from datetime import date
import pytest

from app.models.database import (
    Transaction, SavingsBundle, OtherAsset,
    TransactionType, SavingsStatus, SavingsType,
)
from app.routers.dashboard import get_dashboard_page_data
from tests.conftest import make_transaction

THIS_YEAR  = 2026
THIS_MONTH = 4   # April — matches the test data we reason about


# ── Helpers ───────────────────────────────────────────────────────────────────

def _summary(db, year=THIS_YEAR, month=THIS_MONTH):
    return get_dashboard_page_data(db, year=year, month=month)["summary"]


# ── Savings Rate ──────────────────────────────────────────────────────────────

def test_savings_rate_zero_when_no_income(db_session, tiet_kiem_cat):
    """No income → savings rate must be 0 (avoid division by zero)."""
    make_transaction(db_session, date_val=date(THIS_YEAR, THIS_MONTH, 1),
                     amount=10_000_000, type_=TransactionType.EXPENSE,
                     category_id=tiet_kiem_cat.id)
    s = _summary(db_session)
    assert s["savings_rate"] == 0


def test_savings_rate_uses_tiet_kiem_and_bds(db_session, income_cat, tiet_kiem_cat, bds_cat):
    """Savings rate = (Tiết kiệm + Bất động sản) / income × 100."""
    make_transaction(db_session, date_val=date(THIS_YEAR, THIS_MONTH, 1),
                     amount=100_000_000, type_=TransactionType.INCOME,
                     category_id=income_cat.id)
    make_transaction(db_session, date_val=date(THIS_YEAR, THIS_MONTH, 2),
                     amount=50_000_000, type_=TransactionType.EXPENSE,
                     category_id=tiet_kiem_cat.id)
    make_transaction(db_session, date_val=date(THIS_YEAR, THIS_MONTH, 3),
                     amount=30_000_000, type_=TransactionType.EXPENSE,
                     category_id=bds_cat.id)

    s = _summary(db_session)
    assert s["savings_rate"] == pytest.approx(80.0, rel=1e-2)


def test_savings_rate_only_tiet_kiem_no_bds(db_session, income_cat, tiet_kiem_cat):
    """Works correctly when only Tiết kiệm has transactions."""
    make_transaction(db_session, date_val=date(THIS_YEAR, THIS_MONTH, 1),
                     amount=80_000_000, type_=TransactionType.INCOME,
                     category_id=income_cat.id)
    make_transaction(db_session, date_val=date(THIS_YEAR, THIS_MONTH, 2),
                     amount=20_000_000, type_=TransactionType.EXPENSE,
                     category_id=tiet_kiem_cat.id)

    s = _summary(db_session)
    assert s["savings_rate"] == pytest.approx(25.0, rel=1e-2)


def test_savings_rate_excludes_other_expense_categories(db_session, income_cat, expense_cat, tiet_kiem_cat):
    """Regular expenses must NOT count toward savings rate."""
    make_transaction(db_session, date_val=date(THIS_YEAR, THIS_MONTH, 1),
                     amount=100_000_000, type_=TransactionType.INCOME,
                     category_id=income_cat.id)
    make_transaction(db_session, date_val=date(THIS_YEAR, THIS_MONTH, 2),
                     amount=40_000_000, type_=TransactionType.EXPENSE,
                     category_id=expense_cat.id)      # Food — should not count
    make_transaction(db_session, date_val=date(THIS_YEAR, THIS_MONTH, 3),
                     amount=20_000_000, type_=TransactionType.EXPENSE,
                     category_id=tiet_kiem_cat.id)    # Tiết kiệm — counts

    s = _summary(db_session)
    assert s["savings_rate"] == pytest.approx(20.0, rel=1e-2)


def test_savings_rate_scoped_to_selected_month(db_session, income_cat, tiet_kiem_cat):
    """Transactions in a different month must not affect the selected month's rate."""
    make_transaction(db_session, date_val=date(THIS_YEAR, THIS_MONTH, 1),
                     amount=100_000_000, type_=TransactionType.INCOME,
                     category_id=income_cat.id)
    # Tiết kiệm in previous month — should not count for THIS_MONTH
    make_transaction(db_session, date_val=date(THIS_YEAR, THIS_MONTH - 1, 15),
                     amount=50_000_000, type_=TransactionType.EXPENSE,
                     category_id=tiet_kiem_cat.id)

    s = _summary(db_session)
    assert s["savings_rate"] == 0.0


# ── Critical checks ───────────────────────────────────────────────────────────

def test_monthly_tiet_kiem_amount(db_session, income_cat, tiet_kiem_cat):
    """monthly_tiet_kiem reflects exact amount deposited this month."""
    make_transaction(db_session, date_val=date(THIS_YEAR, THIS_MONTH, 1),
                     amount=100_000_000, type_=TransactionType.INCOME,
                     category_id=income_cat.id)
    make_transaction(db_session, date_val=date(THIS_YEAR, THIS_MONTH, 5),
                     amount=25_000_000, type_=TransactionType.EXPENSE,
                     category_id=tiet_kiem_cat.id)

    s = _summary(db_session)
    assert s["monthly_tiet_kiem"] == pytest.approx(25_000_000)


def test_monthly_bds_amount(db_session, income_cat, bds_cat):
    """monthly_bds reflects exact amount paid to real-estate project this month."""
    make_transaction(db_session, date_val=date(THIS_YEAR, THIS_MONTH, 1),
                     amount=100_000_000, type_=TransactionType.INCOME,
                     category_id=income_cat.id)
    make_transaction(db_session, date_val=date(THIS_YEAR, THIS_MONTH, 10),
                     amount=30_000_000, type_=TransactionType.EXPENSE,
                     category_id=bds_cat.id)

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
    make_transaction(db_session, date_val=date(2026, 1, 1),
                     amount=100_000_000, type_=TransactionType.INCOME,
                     category_id=income_cat.id)
    make_transaction(db_session, date_val=date(2026, 1, 2),
                     amount=20_000_000, type_=TransactionType.EXPENSE,
                     category_id=expense_cat.id)
    # cash_on_hand = 80_000_000

    # Active savings bundle (future_amount counts)
    bundle = SavingsBundle(
        name="Test Bundle", bank_name="VCB",
        type=SavingsType.FIXED_DEPOSIT,
        initial_deposit=50_000_000, current_amount=50_000_000,
        future_amount=53_000_000,
        interest_rate=6.0,
        start_date=date(2026, 1, 1),
        status=SavingsStatus.ACTIVE,
    )
    db_session.add(bundle)

    # Other asset
    asset = OtherAsset(
        name="Gold", asset_type="gold",
        quantity=1.0, unit="taels",
        purchase_price_vnd=8_000_000, current_value_vnd=9_000_000,
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
    make_transaction(db_session, date_val=date(THIS_YEAR, THIS_MONTH, 1),
                     amount=100_000_000, type_=TransactionType.INCOME,
                     category_id=income_cat.id)
    make_transaction(db_session, date_val=date(THIS_YEAR, THIS_MONTH, 2),
                     amount=30_000_000, type_=TransactionType.EXPENSE,
                     category_id=expense_cat.id, is_savings_related=False)
    make_transaction(db_session, date_val=date(THIS_YEAR, THIS_MONTH, 3),
                     amount=20_000_000, type_=TransactionType.EXPENSE,
                     category_id=tiet_kiem_cat.id, is_savings_related=True)

    s = _summary(db_session)
    assert s["total_expense"] == pytest.approx(30_000_000)
    assert s["total_savings_expense"] == pytest.approx(20_000_000)


def test_net_this_month(db_session, income_cat, expense_cat, tiet_kiem_cat):
    """net_this_month = income − living_expense − savings_expense."""
    make_transaction(db_session, date_val=date(THIS_YEAR, THIS_MONTH, 1),
                     amount=100_000_000, type_=TransactionType.INCOME,
                     category_id=income_cat.id)
    make_transaction(db_session, date_val=date(THIS_YEAR, THIS_MONTH, 2),
                     amount=30_000_000, type_=TransactionType.EXPENSE,
                     category_id=expense_cat.id)
    make_transaction(db_session, date_val=date(THIS_YEAR, THIS_MONTH, 3),
                     amount=50_000_000, type_=TransactionType.EXPENSE,
                     category_id=tiet_kiem_cat.id, is_savings_related=True)

    s = _summary(db_session)
    assert s["net_this_month"] == pytest.approx(20_000_000)
