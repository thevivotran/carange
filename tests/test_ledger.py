"""Tests for app/services/ledger.py — the unified money-flow ledger (Phase 1).

Covers:
  - classify() for every Pot, including conflicting-signal precedence.
  - classify() working on both an ORM Transaction and a lightweight row.
  - classify() (Python) staying in sync with _pot_case_expr() (SQL).
  - The canonical aggregate functions, and the reconciliation identities from
    Plan/cash-on-hand-ledger.md holding on a seeded dataset.
"""

from datetime import date
from types import SimpleNamespace

import pytest

from app.models.database import (
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
    AssetType,
    Transaction,
    TransactionType,
)
from app.services import ledger
from app.services.ledger import Direction, Pot, classify

TODAY = date(2026, 4, 15)


# ── Seed helpers ────────────────────────────────────────────────────────────


def _cat(db, name, type_=TransactionType.EXPENSE, **kwargs):
    cat = Category(name=name, type=type_, color="#3B82F6", icon="circle", **kwargs)
    db.add(cat)
    db.commit()
    db.refresh(cat)
    return cat


def _txn(
    db,
    *,
    category_id,
    amount=100,
    type_=TransactionType.EXPENSE,
    date_val=TODAY,
    is_savings_related=False,
    savings_bundle_id=None,
    project_id=None,
):
    t = Transaction(
        date=date_val,
        amount=amount,
        type=type_,
        category_id=category_id,
        is_savings_related=is_savings_related,
        savings_bundle_id=savings_bundle_id,
        project_id=project_id,
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


def _bundle(db, *, future_amount=0, initial_deposit=0, current_amount=0, status=SavingsStatus.ACTIVE):
    b = SavingsBundle(
        name="Test Bundle",
        bank_name="Test Bank",
        type=SavingsType.SAVINGS_GOAL,
        initial_deposit=initial_deposit,
        current_amount=current_amount,
        future_amount=future_amount,
        start_date=TODAY,
        status=status,
    )
    db.add(b)
    db.commit()
    db.refresh(b)
    return b


def _project(db, *, type_=ProjectType.CUSTOM, target_amount=0, current_amount=0):
    p = FinancialProject(
        name="Test Project",
        type=type_,
        target_amount=target_amount,
        current_amount=current_amount,
        priority=Priority.MEDIUM,
        status=ProjectStatus.IN_PROGRESS,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def _load(db, txn_id):
    """Reload a transaction with its category eagerly available for classify()."""
    from sqlalchemy.orm import joinedload

    return db.query(Transaction).options(joinedload(Transaction.category)).filter(Transaction.id == txn_id).one()


# ── classify(): one case per Pot ─────────────────────────────────────────────


def test_classify_external_income(db_session, income_cat):
    t = _txn(db_session, category_id=income_cat.id, type_=TransactionType.INCOME, amount=5000)
    direction, pot = classify(_load(db_session, t.id))
    assert direction is Direction.IN
    assert pot is Pot.EXTERNAL


def test_classify_external_expense(db_session, expense_cat):
    t = _txn(db_session, category_id=expense_cat.id, type_=TransactionType.EXPENSE, amount=300)
    direction, pot = classify(_load(db_session, t.id))
    assert direction is Direction.OUT
    assert pot is Pot.EXTERNAL


def test_classify_liquid_savings_via_kpi_role(db_session, tiet_kiem_cat):
    """tiet_kiem_cat fixture sets kpi_role='liquid_savings', no other flags."""
    t = _txn(db_session, category_id=tiet_kiem_cat.id, amount=200)
    _, pot = classify(_load(db_session, t.id))
    assert pot is Pot.LIQUID_SAVINGS


def test_classify_liquid_savings_via_is_savings_category_flag(db_session):
    cat = _cat(db_session, "Gửi tiết kiệm khác", is_savings_category=True)
    t = _txn(db_session, category_id=cat.id, amount=150)
    _, pot = classify(_load(db_session, t.id))
    assert pot is Pot.LIQUID_SAVINGS


def test_classify_liquid_savings_via_txn_is_savings_related_flag(db_session, expense_cat):
    """A plain category with no role/flag, but the transaction itself is tagged."""
    t = _txn(db_session, category_id=expense_cat.id, amount=75, is_savings_related=True)
    _, pot = classify(_load(db_session, t.id))
    assert pot is Pot.LIQUID_SAVINGS


def test_classify_liquid_savings_via_savings_bundle_fk(db_session, expense_cat):
    """A plain category, no role/flag — but linked to a specific bundle by FK."""
    bundle = _bundle(db_session)
    t = _txn(db_session, category_id=expense_cat.id, amount=500, savings_bundle_id=bundle.id)
    _, pot = classify(_load(db_session, t.id))
    assert pot is Pot.LIQUID_SAVINGS


def test_classify_real_estate_via_kpi_role(db_session, bds_cat):
    t = _txn(db_session, category_id=bds_cat.id, amount=10_000)
    _, pot = classify(_load(db_session, t.id))
    assert pot is Pot.REAL_ESTATE


def test_classify_investment_via_category_name(db_session):
    """No formal flag for investment yet — derived from the Vietnamese category
    name "Đầu tư" (see ledger.INVESTMENT_CATEGORY_NAME TODO)."""
    cat = _cat(db_session, ledger.INVESTMENT_CATEGORY_NAME)
    t = _txn(db_session, category_id=cat.id, amount=1_000)
    _, pot = classify(_load(db_session, t.id))
    assert pot is Pot.INVESTMENT


def test_classify_investment_name_match_is_exact(db_session):
    """A category that merely contains "Đầu tư" as a substring should NOT match —
    guards against an accidental `in` / `contains` implementation later."""
    cat = _cat(db_session, "Đầu tư ngắn hạn")
    t = _txn(db_session, category_id=cat.id, amount=1_000)
    _, pot = classify(_load(db_session, t.id))
    assert pot is Pot.EXTERNAL


def test_classify_project_via_project_id(db_session, expense_cat):
    project = _project(db_session)
    t = _txn(db_session, category_id=expense_cat.id, amount=2_000, project_id=project.id)
    _, pot = classify(_load(db_session, t.id))
    assert pot is Pot.PROJECT


# ── classify(): conflicting-signal precedence ────────────────────────────────


def test_precedence_real_estate_role_beats_project_fk(db_session, bds_cat):
    """The production case: a real-estate purchase carries BOTH
    kpi_role='real_estate' AND a project_id (RE is modelled as a project).
    Category role must win (rule 1 > 6) so it lands in REAL_ESTATE, not
    PROJECT — otherwise the real_estate pot reads 0."""
    project = _project(db_session, type_=ProjectType.REAL_ESTATE)
    t = _txn(db_session, category_id=bds_cat.id, amount=1_200, project_id=project.id)
    _, pot = classify(_load(db_session, t.id))
    assert pot is Pot.REAL_ESTATE


def test_precedence_liquid_savings_role_beats_project_fk(db_session, tiet_kiem_cat):
    """kpi_role='liquid_savings' plus a project_id -> LIQUID_SAVINGS (rule 2 > 6)."""
    project = _project(db_session)
    t = _txn(db_session, category_id=tiet_kiem_cat.id, amount=900, project_id=project.id)
    _, pot = classify(_load(db_session, t.id))
    assert pot is Pot.LIQUID_SAVINGS


def test_precedence_savings_bundle_beats_project_fk(db_session, expense_cat):
    """A plain category with both a savings_bundle_id and a project_id ->
    LIQUID_SAVINGS (rule 3 > 6): the bundle link outranks the bare project
    link."""
    bundle = _bundle(db_session)
    project = _project(db_session)
    t = _txn(
        db_session,
        category_id=expense_cat.id,
        amount=1_200,
        savings_bundle_id=bundle.id,
        project_id=project.id,
    )
    _, pot = classify(_load(db_session, t.id))
    assert pot is Pot.LIQUID_SAVINGS


def test_precedence_real_estate_role_beats_savings_bundle(db_session, bds_cat):
    """bds_cat (kpi_role='real_estate') with a savings_bundle_id FK -> the
    category role (rule 1) outranks the bundle FK (rule 3)."""
    bundle = _bundle(db_session)
    t = _txn(db_session, category_id=bds_cat.id, amount=900, savings_bundle_id=bundle.id)
    _, pot = classify(_load(db_session, t.id))
    assert pot is Pot.REAL_ESTATE


def test_precedence_real_estate_role_beats_savings_related_flag(db_session, bds_cat):
    """bds_cat (kpi_role='real_estate') with is_savings_related=True on the txn
    -> REAL_ESTATE wins (rule 1 > 4): the category-level role outranks the
    generic is_savings_related flag."""
    t = _txn(db_session, category_id=bds_cat.id, amount=850, is_savings_related=True)
    _, pot = classify(_load(db_session, t.id))
    assert pot is Pot.REAL_ESTATE


def test_precedence_savings_bundle_beats_investment_name(db_session):
    """A category named "Đầu tư" with a savings_bundle_id FK -> LIQUID_SAVINGS
    (rule 3 > 5): the bundle link outranks the name-based investment fallback."""
    cat = _cat(db_session, ledger.INVESTMENT_CATEGORY_NAME)
    bundle = _bundle(db_session)
    t = _txn(db_session, category_id=cat.id, amount=700, savings_bundle_id=bundle.id)
    _, pot = classify(_load(db_session, t.id))
    assert pot is Pot.LIQUID_SAVINGS


def test_precedence_liquid_savings_role_beats_investment_name(db_session):
    """A category named "Đầu tư" that ALSO carries kpi_role='liquid_savings'
    -> LIQUID_SAVINGS wins (rule 2 > 5): the formal role outranks the
    name-based investment fallback."""
    cat = _cat(db_session, ledger.INVESTMENT_CATEGORY_NAME, kpi_role="liquid_savings")
    t = _txn(db_session, category_id=cat.id, amount=650)
    _, pot = classify(_load(db_session, t.id))
    assert pot is Pot.LIQUID_SAVINGS


def test_classify_project_pot_requires_no_more_specific_signal(db_session, expense_cat):
    """PROJECT only fires when no category-nature signal matches: a plain
    (roleless, flagless, bundleless) category linked to a project -> PROJECT."""
    project = _project(db_session)
    t = _txn(db_session, category_id=expense_cat.id, amount=2_000, project_id=project.id)
    _, pot = classify(_load(db_session, t.id))
    assert pot is Pot.PROJECT


def test_classify_direction_in_for_savings_bundle_return(db_session, expense_cat):
    """An INCOME transaction linked to a bundle (e.g. a payout) is still
    LIQUID_SAVINGS, but Direction.IN (money returns to cash)."""
    bundle = _bundle(db_session)
    t = _txn(
        db_session,
        category_id=expense_cat.id,
        type_=TransactionType.INCOME,
        amount=300,
        savings_bundle_id=bundle.id,
    )
    direction, pot = classify(_load(db_session, t.id))
    assert direction is Direction.IN
    assert pot is Pot.LIQUID_SAVINGS


# ── classify(): works on lightweight rows, not just ORM objects ─────────────


def test_classify_works_on_namedtuple_with_nested_category(db_session):
    fake_category = SimpleNamespace(kpi_role="real_estate", is_savings_category=False, name="Bất động sản")
    fake_txn = SimpleNamespace(
        type=TransactionType.EXPENSE,
        is_savings_related=False,
        savings_bundle_id=None,
        project_id=None,
        category=fake_category,
    )
    direction, pot = classify(fake_txn)
    assert direction is Direction.OUT
    assert pot is Pot.REAL_ESTATE


def test_classify_works_on_row_with_flattened_category_attrs(db_session):
    """Simulates a raw SQL join row where category fields are flattened onto
    the row itself (kpi_role / is_savings_category / category_name) instead
    of nested under `.category`."""
    fake_row = SimpleNamespace(
        type="income",
        is_savings_related=False,
        savings_bundle_id=None,
        project_id=None,
        category=None,
        kpi_role=None,
        is_savings_category=False,
        category_name=ledger.INVESTMENT_CATEGORY_NAME,
    )
    direction, pot = classify(fake_row)
    assert direction is Direction.IN
    assert pot is Pot.INVESTMENT


def test_classify_accepts_raw_string_type(db_session, income_cat):
    """transaction.type as a raw lowercase string (not the enum member)."""
    fake_row = SimpleNamespace(
        type="income",
        is_savings_related=False,
        savings_bundle_id=None,
        project_id=None,
        category=None,
    )
    direction, pot = classify(fake_row)
    assert direction is Direction.IN
    assert pot is Pot.EXTERNAL


# ── classify() (Python) stays in sync with _pot_case_expr() (SQL) ───────────


def test_classify_matches_sql_pot_expression(db_session, income_cat, expense_cat, tiet_kiem_cat, bds_cat):
    """Cross-checks the Python classifier against the SQL CASE expression used
    by the aggregate functions, across one transaction per Pot. If someone
    edits one without the other, this test catches the drift."""
    bundle = _bundle(db_session)
    project = _project(db_session)
    investment_cat = _cat(db_session, ledger.INVESTMENT_CATEGORY_NAME)

    ids = [
        _txn(db_session, category_id=income_cat.id, type_=TransactionType.INCOME, amount=100).id,
        _txn(db_session, category_id=expense_cat.id, amount=100).id,
        _txn(db_session, category_id=tiet_kiem_cat.id, amount=100).id,
        _txn(db_session, category_id=bds_cat.id, amount=100).id,
        _txn(db_session, category_id=investment_cat.id, amount=100).id,
        _txn(db_session, category_id=expense_cat.id, amount=100, project_id=project.id).id,
        _txn(db_session, category_id=expense_cat.id, amount=100, savings_bundle_id=bundle.id).id,
    ]

    pot_expr = ledger._pot_case_expr()
    for txn_id in ids:
        py_direction, py_pot = classify(_load(db_session, txn_id))
        sql_pot_value = (
            db_session.query(pot_expr)
            .select_from(Transaction)
            .join(Category, Transaction.category_id == Category.id)
            .filter(Transaction.id == txn_id)
            .scalar()
        )
        assert py_pot.value == sql_pot_value, f"txn {txn_id}: python={py_pot.value} sql={sql_pot_value}"


# ── Aggregate functions ──────────────────────────────────────────────────────


def test_pot_balance_rejects_external():
    with pytest.raises(ValueError):
        ledger.pot_balance(None, Pot.EXTERNAL)  # db unused before the guard


def test_aggregate_functions_and_reconciliation_identities(db_session, income_cat, expense_cat, tiet_kiem_cat, bds_cat):
    """Seeds one transaction per pot (both directions where relevant) and
    checks the reconciliation identities from Plan/cash-on-hand-ledger.md:

        external_income - external_expense == net_family_surplus
        liquid_cash == Σincome - Σexpense (raw)
        net_family_surplus == liquid_cash + Σ pot_balance(internal pots)
    """
    investment_cat = _cat(db_session, ledger.INVESTMENT_CATEGORY_NAME)
    bundle = _bundle(db_session)
    project = _project(db_session)

    # EXTERNAL: 1_000_000 in, 300_000 out.
    _txn(db_session, category_id=income_cat.id, type_=TransactionType.INCOME, amount=1_000_000)
    _txn(db_session, category_id=expense_cat.id, amount=300_000)

    # LIQUID_SAVINGS: 200_000 deposit (out), 50_000 return (in) -> balance 150_000.
    _txn(db_session, category_id=tiet_kiem_cat.id, amount=200_000)
    _txn(
        db_session, category_id=expense_cat.id, type_=TransactionType.INCOME, amount=50_000, savings_bundle_id=bundle.id
    )

    # REAL_ESTATE: 400_000 deposit (out) only -> balance 400_000.
    _txn(db_session, category_id=bds_cat.id, amount=400_000)

    # INVESTMENT: 150_000 deposit (out) only -> balance 150_000.
    _txn(db_session, category_id=investment_cat.id, amount=150_000)

    # PROJECT: 250_000 payment (out), 20_000 refund (in) -> balance 230_000.
    _txn(db_session, category_id=expense_cat.id, amount=250_000, project_id=project.id)
    _txn(db_session, category_id=expense_cat.id, type_=TransactionType.INCOME, amount=20_000, project_id=project.id)

    assert ledger.external_income(db_session) == 1_000_000
    assert ledger.external_expense(db_session) == 300_000
    assert ledger.net_family_surplus(db_session) == 700_000

    # liquid_cash == raw sum(income) - sum(expense) over ALL pots.
    total_income = 1_000_000 + 50_000 + 20_000
    total_expense = 300_000 + 200_000 + 400_000 + 150_000 + 250_000
    assert ledger.liquid_cash(db_session) == total_income - total_expense
    assert ledger.get_cash_on_hand(db_session) == ledger.liquid_cash(db_session)  # alias

    assert ledger.pot_balance(db_session, Pot.LIQUID_SAVINGS) == 150_000
    assert ledger.pot_balance(db_session, Pot.REAL_ESTATE) == 400_000
    assert ledger.pot_balance(db_session, Pot.INVESTMENT) == 150_000
    assert ledger.pot_balance(db_session, Pot.PROJECT) == 230_000

    internal_total = sum(ledger.pot_balance(db_session, p) for p in ledger.INTERNAL_POTS)
    assert internal_total == 930_000

    # Core identity: transfers cancel — the family's true surplus equals the
    # spendable-cash delta plus everything parked in internal pots.
    assert ledger.net_family_surplus(db_session) == ledger.liquid_cash(db_session) + internal_total


def test_net_worth_matches_liquid_cash_plus_pot_balances_for_seeded_pots(db_session, income_cat, tiet_kiem_cat):
    """net_worth() reproduces dashboard_service's current formula (cash +
    SavingsBundle.future_amount + OtherAsset.current_value_vnd + PAID
    ProjectPayment amounts) unchanged (Phase 1 constraint — no VALUE change).

    To verify "net_worth == liquid_cash + Σ pot balances" against that exact
    formula, this dataset is engineered so each "stock" table matches its
    corresponding pot's transaction-derived balance:
      - SavingsBundle.future_amount is set equal to pot_balance(LIQUID_SAVINGS).
      - ProjectPayment (PAID) totals equal pot_balance(PROJECT).
      - No OtherAsset rows, and no REAL_ESTATE/INVESTMENT transactions, since
        Phase 1's net_worth formula has no stock source for those two pots
        yet (that mapping is formalized in a later phase — see
        Plan/cash-on-hand-ledger.md Phase 4).
    """
    project = _project(db_session, type_=ProjectType.CUSTOM)

    # External flow, so liquid_cash isn't trivially zero.
    _txn(db_session, category_id=income_cat.id, type_=TransactionType.INCOME, amount=2_000_000)

    # LIQUID_SAVINGS: net deposit of 500_000.
    _txn(db_session, category_id=tiet_kiem_cat.id, amount=500_000)
    savings_balance = ledger.pot_balance(db_session, Pot.LIQUID_SAVINGS)
    assert savings_balance == 500_000
    _bundle(db_session, future_amount=savings_balance, initial_deposit=savings_balance, current_amount=savings_balance)

    # PROJECT: a paid payment whose amount matches the linked transaction.
    _txn(db_session, category_id=income_cat.id, amount=300_000, type_=TransactionType.EXPENSE, project_id=project.id)
    project_balance = ledger.pot_balance(db_session, Pot.PROJECT)
    assert project_balance == 300_000
    payment = ProjectPayment(project_id=project.id, amount=project_balance, status=PaymentStatus.PAID, due_date=TODAY)
    db_session.add(payment)
    db_session.commit()

    assert ledger.pot_balance(db_session, Pot.REAL_ESTATE) == 0
    assert ledger.pot_balance(db_session, Pot.INVESTMENT) == 0

    internal_total = sum(ledger.pot_balance(db_session, p) for p in ledger.INTERNAL_POTS)
    assert internal_total == savings_balance + project_balance

    expected = ledger.liquid_cash(db_session) + internal_total
    assert ledger.net_worth(db_session) == expected


def test_net_worth_includes_other_assets(db_session, income_cat):
    """OtherAsset (gold/currency holdings) has no corresponding Pot in Phase 1
    — it's added to net_worth as a flat current-state figure, same as today."""
    _txn(db_session, category_id=income_cat.id, type_=TransactionType.INCOME, amount=100_000)
    asset = OtherAsset(
        name="Gold bar",
        asset_type=AssetType.GOLD,
        quantity=1.0,
        unit="tael",
        purchase_price_vnd=80_000_000,
        current_value_vnd=85_000_000,
    )
    db_session.add(asset)
    db_session.commit()

    assert ledger.net_worth(db_session) == ledger.liquid_cash(db_session) + 85_000_000


def test_soft_deleted_transactions_excluded_from_every_aggregate(db_session, income_cat, expense_cat, tiet_kiem_cat):
    from datetime import datetime, timezone

    t = _txn(db_session, category_id=income_cat.id, type_=TransactionType.INCOME, amount=999_999)
    t.deleted_at = datetime.now(timezone.utc)
    db_session.commit()

    t2 = _txn(db_session, category_id=tiet_kiem_cat.id, amount=888_888)
    t2.deleted_at = datetime.now(timezone.utc)
    db_session.commit()

    assert ledger.external_income(db_session) == 0
    assert ledger.external_expense(db_session) == 0
    assert ledger.net_family_surplus(db_session) == 0
    assert ledger.liquid_cash(db_session) == 0
    assert ledger.pot_balance(db_session, Pot.LIQUID_SAVINGS) == 0
    assert ledger.net_worth(db_session) == 0
