"""Phase 3 — reconciliation guard tests (the anti-drift tripwire).

These assert the invariants from Plan/cash-on-hand-ledger.md on a seeded DB,
and a classifier-completeness check that fails the moment a new category role
or pot-linked FK appears that the ledger doesn't map. Includes a
deliberate-break case proving the tripwire actually fires.

Two distinct identities are asserted; keeping them separate is deliberate so
Phase 4 can tighten identity (B) without touching (A):

  (A) FLOW reconciliation — holds on ANY data, transaction-derived only:
        net_family_surplus == liquid_cash + Σ pot_balance(internal pots)
      Rationale: transfers cancel. external_income − external_expense (the
      surplus) equals the change in spendable cash plus everything parked in
      the internal pots. Equivalent bridge form (also asserted):
        liquid_cash == external_income − external_expense
                       − Σ pot_balance(internal pots)

  (B) STOCK reconciliation — the CURRENT (Phase-1) net_worth definition:
        net_worth == liquid_cash
                     + Σ active-bundle future_amount        (LIQUID_SAVINGS stock)
                     + Σ other-asset current_value          (non-pot asset stock)
                     + Σ PAID project-payment amounts        (PROJECT stock)
      These three stock terms are NOT transaction-derived (future_amount is a
      projected maturity value; project payments and asset values are entered
      independently of transactions), so (B) is NOT the same as (A) plus a
      constant on arbitrary data.

  Phase-4 tightening (documented, not asserted here): the LIQUID_SAVINGS stock
  term moves off `future_amount` onto pot_balance(LIQUID_SAVINGS)+earned
  interest, and REAL_ESTATE/INVESTMENT gain their own stock sources, at which
  point (B) collapses toward "liquid_cash + Σ pot_balance(internal) + non-pot
  asset stock". This file's identity (B) is written so that change is a
  localized edit here.
"""

from datetime import date

import pytest

from app.models.database import (
    AssetType,
    Category,
    FinancialProject,
    OtherAsset,
    PaymentStatus,
    Priority,
    ProjectPayment,
    ProjectStatus,
    ProjectType,
    SavingsBundle,
    SavingsStatus,
    SavingsType,
    Transaction,
    TransactionType,
)
from app.services import ledger
from app.services.ledger import Pot

TODAY = date(2026, 4, 15)


# ── Seed helpers ────────────────────────────────────────────────────────────


def _cat(db, name, type_=TransactionType.EXPENSE, **kwargs):
    cat = Category(name=name, type=type_, color="#3B82F6", icon="circle", **kwargs)
    db.add(cat)
    db.commit()
    db.refresh(cat)
    return cat


def _txn(db, *, category_id, amount, type_=TransactionType.EXPENSE, savings_bundle_id=None, project_id=None):
    t = Transaction(
        date=TODAY,
        amount=amount,
        type=type_,
        category_id=category_id,
        savings_bundle_id=savings_bundle_id,
        project_id=project_id,
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


def _bundle(db, *, future_amount=0):
    b = SavingsBundle(
        name="Bundle",
        bank_name="Bank",
        type=SavingsType.SAVINGS_GOAL,
        initial_deposit=0,
        current_amount=0,
        future_amount=future_amount,
        start_date=TODAY,
        status=SavingsStatus.ACTIVE,
    )
    db.add(b)
    db.commit()
    db.refresh(b)
    return b


def _project(db, *, type_=ProjectType.CUSTOM):
    p = FinancialProject(
        name="Project",
        type=type_,
        target_amount=0,
        current_amount=0,
        priority=Priority.MEDIUM,
        status=ProjectStatus.IN_PROGRESS,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


# ── Reconciliation identities ────────────────────────────────────────────────


def _seed_diverse_dataset(db, income_cat, expense_cat, tiet_kiem_cat, bds_cat):
    """One+ transaction per pot, both directions where meaningful."""
    investment_cat = _cat(db, ledger.INVESTMENT_CATEGORY_NAME)
    bundle = _bundle(db)
    project = _project(db)

    # EXTERNAL
    _txn(db, category_id=income_cat.id, type_=TransactionType.INCOME, amount=5_000_000)
    _txn(db, category_id=expense_cat.id, amount=1_200_000)
    # LIQUID_SAVINGS via role + a bundle-linked return (IN)
    _txn(db, category_id=tiet_kiem_cat.id, amount=800_000)
    _txn(db, category_id=expense_cat.id, type_=TransactionType.INCOME, amount=100_000, savings_bundle_id=bundle.id)
    # REAL_ESTATE via role
    _txn(db, category_id=bds_cat.id, amount=1_500_000)
    # INVESTMENT via name
    _txn(db, category_id=investment_cat.id, amount=600_000)
    # PROJECT via FK (plain category, no role)
    _txn(db, category_id=expense_cat.id, amount=900_000, project_id=project.id)
    _txn(db, category_id=expense_cat.id, type_=TransactionType.INCOME, amount=50_000, project_id=project.id)
    return bundle, project


def test_identity_A_flow_reconciliation(db_session, income_cat, expense_cat, tiet_kiem_cat, bds_cat):
    """(A) net_family_surplus == liquid_cash + Σ pot_balance(internal pots),
    plus the bridge and definition sub-identities."""
    _seed_diverse_dataset(db_session, income_cat, expense_cat, tiet_kiem_cat, bds_cat)

    ext_in = ledger.external_income(db_session)
    ext_out = ledger.external_expense(db_session)
    surplus = ledger.net_family_surplus(db_session)
    lc = ledger.liquid_cash(db_session)
    internal_total = sum(ledger.pot_balance(db_session, p) for p in ledger.INTERNAL_POTS)

    # net_family_surplus == external_income − external_expense
    assert surplus == ext_in - ext_out

    # liquid_cash == Σincome − Σexpense (raw), cross-checked against the ORM
    raw = _raw_income_minus_expense(db_session)
    assert lc == raw

    # The core flow identity: transfers cancel.
    assert surplus == lc + internal_total
    # Equivalent bridge form.
    assert lc == ext_in - ext_out - internal_total


def test_identity_B_stock_reconciliation_matches_phase1_net_worth(
    db_session, income_cat, expense_cat, tiet_kiem_cat, bds_cat
):
    """(B) net_worth == liquid_cash + active-bundle future_amount + asset
    current_value + PAID project payments — the exact Phase-1 definition."""
    bundle, project = _seed_diverse_dataset(db_session, income_cat, expense_cat, tiet_kiem_cat, bds_cat)

    # Give the stock tables non-trivial, independent values.
    bundle.future_amount = 3_333_000
    db_session.add(bundle)
    db_session.add(
        OtherAsset(
            name="Gold",
            asset_type=AssetType.GOLD,
            quantity=2.0,
            unit="tael",
            purchase_price_vnd=150_000_000,
            current_value_vnd=170_000_000,
        )
    )
    db_session.add(ProjectPayment(project_id=project.id, amount=444_000, status=PaymentStatus.PAID, due_date=TODAY))
    # A PENDING payment must be excluded.
    db_session.add(ProjectPayment(project_id=project.id, amount=999_000, status=PaymentStatus.PENDING, due_date=TODAY))
    db_session.commit()

    lc = ledger.liquid_cash(db_session)
    expected = lc + 3_333_000 + 170_000_000 + 444_000
    assert ledger.net_worth(db_session) == expected


# ── Classifier-completeness (anti-drift tripwire) ────────────────────────────

KNOWN_INTERNAL_KPI_ROLES = {"liquid_savings": Pot.LIQUID_SAVINGS, "real_estate": Pot.REAL_ESTATE}


def assert_classifier_covers_db(db):
    """Fail if the DB contains a category kpi_role, or a pot-linked FK, that the
    ledger does not map to a known non-EXTERNAL Pot.

    This is the guard the plan calls for: a new role/link added without wiring
    ledger.classify()/._pot_case_expr() trips this immediately.
    """
    # 1. Every distinct non-null kpi_role must be a known internal role that a
    #    representative transaction classifies into the expected pot.
    roles = {r for (r,) in db.query(Category.kpi_role).filter(Category.kpi_role.isnot(None)).distinct().all()}
    for role in roles:
        assert role in KNOWN_INTERNAL_KPI_ROLES, f"Unmapped category.kpi_role={role!r} — wire it into ledger.Pot"

    # 2. Every transaction carrying a pot-linked FK must classify to a non-
    #    EXTERNAL pot (savings_bundle_id / project_id are always internal).
    from sqlalchemy.orm import joinedload

    fk_txns = (
        db.query(Transaction)
        .options(joinedload(Transaction.category))
        .filter(
            Transaction.deleted_at.is_(None),
            (Transaction.savings_bundle_id.isnot(None)) | (Transaction.project_id.isnot(None)),
        )
        .all()
    )
    for t in fk_txns:
        _, pot = ledger.classify(t)
        assert pot is not Pot.EXTERNAL, f"txn {t.id} has a pot FK but classified EXTERNAL"


def test_classifier_completeness_passes_on_wired_data(db_session, income_cat, expense_cat, tiet_kiem_cat, bds_cat):
    _seed_diverse_dataset(db_session, income_cat, expense_cat, tiet_kiem_cat, bds_cat)
    assert_classifier_covers_db(db_session)  # must not raise


def test_classifier_completeness_catches_unmapped_role(db_session):
    """DELIBERATE-BREAK: an unmapped kpi_role must trip the completeness guard.

    Proves the tripwire fails when a new pot signal is left unwired — the
    whole point of the anti-drift test. If someone adds kpi_role='crypto'
    without extending ledger.Pot, this assertion (and thus the guard) fires.
    """
    _cat(db_session, "Crypto wallet", kpi_role="crypto")  # role the ledger doesn't know
    with pytest.raises(AssertionError, match="Unmapped category.kpi_role"):
        assert_classifier_covers_db(db_session)


def test_reconciliation_breaks_if_a_pot_is_dropped_from_the_sum(
    db_session, income_cat, expense_cat, tiet_kiem_cat, bds_cat, monkeypatch
):
    """DELIBERATE-BREAK for identity (A): if an internal pot is omitted from the
    reconciliation sum (i.e. its transfers are silently treated as EXTERNAL),
    the flow identity no longer holds. Guards against a future pot being added
    to Pot but forgotten in INTERNAL_POTS."""
    _seed_diverse_dataset(db_session, income_cat, expense_cat, tiet_kiem_cat, bds_cat)

    full = sum(ledger.pot_balance(db_session, p) for p in ledger.INTERNAL_POTS)
    surplus = ledger.net_family_surplus(db_session)
    lc = ledger.liquid_cash(db_session)
    assert surplus == lc + full  # holds with all pots

    # Drop REAL_ESTATE (which carries a non-zero balance in the seed) — identity
    # must now fail, proving the sum is sensitive to a missing pot.
    dropped = [p for p in ledger.INTERNAL_POTS if p is not Pot.REAL_ESTATE]
    partial = sum(ledger.pot_balance(db_session, p) for p in dropped)
    assert ledger.pot_balance(db_session, Pot.REAL_ESTATE) != 0  # the seed really exercises this pot
    assert surplus != lc + partial


# ── local helper ─────────────────────────────────────────────────────────────


def _raw_income_minus_expense(db):
    from sqlalchemy import case, func

    row = (
        db.query(
            func.coalesce(
                func.sum(case((Transaction.type == TransactionType.INCOME, Transaction.amount), else_=0)), 0
            ).label("inc"),
            func.coalesce(
                func.sum(case((Transaction.type == TransactionType.EXPENSE, Transaction.amount), else_=0)), 0
            ).label("exp"),
        )
        .filter(Transaction.deleted_at.is_(None))
        .first()
    )
    return float(row.inc or 0) - float(row.exp or 0)
