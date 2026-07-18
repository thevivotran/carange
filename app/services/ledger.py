"""Unified money-flow ledger — the single source of truth for classifying every
transaction as a directed edge between the family's spendable CASH and one
counterparty pot, and for the canonical aggregate money figures derived from
that classification.

See Plan/cash-on-hand-ledger.md for the full problem statement. In short:
the app previously scattered classification signals across 8 overlapping
fields (`transaction.type`, `is_savings_related`, `savings_bundle_id`,
`project_id`, `category.kpi_role`, `is_savings_category`, `is_wealth_building`,
`spend_nature`) that no single function read consistently. That drift is what
dragged `cash_on_hand` deeply negative even though the family's true surplus
was positive: depositing into savings/real-estate is recorded as a single-leg
`expense`, subtracting from cash while *also* being added back into net worth
as a pot balance.

Every transaction is a directed edge between CASH and exactly one pot:

    Pot ∈ { EXTERNAL, LIQUID_SAVINGS, REAL_ESTATE, INVESTMENT, PROJECT }

    EXTERNAL, income  -> real external income   (salary, gift, cashback)
    EXTERNAL, expense -> real consumption       (food, bills, wedding)
    <internal pot>    -> TRANSFER               (net-zero to net worth)

This module is Phase 1 of the plan: it introduces `classify()` and the
canonical aggregate helpers. It intentionally does NOT change any consumer
service (dashboard/forecast/savings) and does NOT change the net_worth VALUE
— `net_worth()` here reproduces dashboard_service's current formula exactly
(cash + SavingsBundle.future_amount + OtherAsset.current_value_vnd +
ProjectPayment PAID amount). Wiring consumers onto this module is Phase 2.
"""

import enum

from sqlalchemy import and_, case, func, or_
from sqlalchemy.orm import Session

from app.models.database import (
    Category,
    OtherAsset,
    PaymentStatus,
    ProjectPayment,
    SavingsBundle,
    SavingsStatus,
    Transaction,
    TransactionType,
)

# Vietnamese name for the household's investment category. As of Phase 4 the
# PRIMARY investment signal is `category.kpi_role == 'investment'` (formalized
# alongside liquid_savings/real_estate — see migration 0035). This name match
# is retained only as a SECONDARY fallback so any pre-migration or un-tagged
# "Đầu tư" rows still classify correctly; a category rename would break the
# fallback but not the role, which is why the role is now primary.
INVESTMENT_CATEGORY_NAME = "Đầu tư"


class Pot(enum.Enum):
    """The counterparty side of a money movement, relative to spendable CASH."""

    EXTERNAL = "external"
    LIQUID_SAVINGS = "liquid_savings"
    REAL_ESTATE = "real_estate"
    INVESTMENT = "investment"
    PROJECT = "project"


# Every pot except EXTERNAL is "internal" — a transfer that nets to zero
# against net worth (money didn't leave the family, it moved pots).
INTERNAL_POTS: tuple[Pot, ...] = (
    Pot.LIQUID_SAVINGS,
    Pot.REAL_ESTATE,
    Pot.INVESTMENT,
    Pot.PROJECT,
)


class Direction(enum.Enum):
    """Which way money moves relative to CASH, derived from `transaction.type`."""

    IN = "in"  # cash increases (an income-type transaction)
    OUT = "out"  # cash decreases (an expense-type transaction)


def _direction_of(txn_type) -> Direction:
    """Normalize `transaction.type` (enum member or raw string) to a Direction."""
    value = txn_type.value if isinstance(txn_type, TransactionType) else str(txn_type).lower()
    return Direction.IN if value == TransactionType.INCOME.value else Direction.OUT


def _category_signals(txn) -> tuple[str | None, bool, str | None]:
    """Extract (kpi_role, is_savings_category, category_name) from *txn*.

    Supports two shapes so `classify()` works on both an ORM Transaction (with
    a loaded `.category` relationship) and a lightweight row/namedtuple that
    either nests the same `.category` object or exposes the category's
    attributes flattened directly onto the row as `kpi_role`,
    `is_savings_category`, `category_name` (e.g. a raw SQL join result).
    """
    category = getattr(txn, "category", None)
    if category is not None:
        return (
            getattr(category, "kpi_role", None),
            bool(getattr(category, "is_savings_category", False)),
            getattr(category, "name", None),
        )
    return (
        getattr(txn, "kpi_role", None),
        bool(getattr(txn, "is_savings_category", False)),
        getattr(txn, "category_name", None),
    )


def classify(txn) -> tuple[Direction, Pot]:
    """Classify a transaction as a directed edge between CASH and one Pot.

    Consolidates the 8 scattered flags behind one function. Works on an ORM
    `Transaction` object or a lightweight row/namedtuple exposing the same
    attributes (`type`, `is_savings_related`, `savings_bundle_id`,
    `project_id`, and category info — see `_category_signals`).

    Precedence when multiple internal signals match on the same transaction.

    The guiding rule (user decision, 2026-07-18): a transaction's *nature* —
    what kind of money movement it is, as declared by its category's role —
    outranks the FK instance links that merely say *which* row it touches. In
    production, real-estate transactions carry BOTH `category.kpi_role ==
    'real_estate'` AND a `project_id` (RE purchases are modelled as
    FinancialProjects); the category role must win so they land in the
    REAL_ESTATE pot rather than being swept into a generic PROJECT bucket.

    Order (first match wins):

      1. `category.kpi_role == 'real_estate'`   -> REAL_ESTATE
         Category-nature signal; ranks above `project_id` (rule 6) so RE
         purchases modelled as projects classify as real estate.
      2. `category.kpi_role == 'liquid_savings'` -> LIQUID_SAVINGS
         Category-nature signal.
      3. `savings_bundle_id IS NOT NULL`         -> LIQUID_SAVINGS
         FK to a specific SavingsBundle. Below the explicit savings roles but
         above the weaker savings flags, since it names a concrete bundle.
      4. `category.is_savings_category`
         OR `transaction.is_savings_related`     -> LIQUID_SAVINGS
         Generic savings flags — no role, no bundle FK.
      5. `category.kpi_role == 'investment'`     -> INVESTMENT
         PRIMARY investment signal (Phase 4 role, migration 0035).
      6. `category.name == 'Đầu tư'`             -> INVESTMENT
         SECONDARY fallback for pre-migration / un-tagged rows (see
         INVESTMENT_CATEGORY_NAME).
      7. `project_id IS NOT NULL`                -> PROJECT
         FK to a FinancialProject. Ranks LAST among internal signals: a
         category-nature classification (rules 1-6) always wins over the bare
         project link, so only projects with no more specific signal land here.
      8. else                                    -> EXTERNAL
    """
    direction = _direction_of(txn.type)
    kpi_role, is_savings_category, category_name = _category_signals(txn)

    if kpi_role == "real_estate":
        return direction, Pot.REAL_ESTATE

    if kpi_role == "liquid_savings":
        return direction, Pot.LIQUID_SAVINGS

    if getattr(txn, "savings_bundle_id", None) is not None:
        return direction, Pot.LIQUID_SAVINGS

    if is_savings_category or bool(getattr(txn, "is_savings_related", False)):
        return direction, Pot.LIQUID_SAVINGS

    if kpi_role == "investment":  # PRIMARY investment signal
        return direction, Pot.INVESTMENT

    if category_name == INVESTMENT_CATEGORY_NAME:  # SECONDARY fallback
        return direction, Pot.INVESTMENT

    if getattr(txn, "project_id", None) is not None:
        return direction, Pot.PROJECT

    return direction, Pot.EXTERNAL


def _pot_case_expr():
    """SQL CASE expression mirroring `classify()`'s pot precedence exactly.

    Used by the aggregate functions below so they can compute in one SQL pass
    (case/sum) instead of loading every row into Python. Requires the query to
    join `transactions` to `categories` (category_id is NOT NULL, so an INNER
    JOIN is safe and lossless).

    NOTE: this must be kept in sync with `classify()` by hand — there is no
    single source both run through. `test_ledger.py` cross-checks the two
    implementations against each other on seeded data as a regression guard.
    """
    return case(
        (Category.kpi_role == "real_estate", Pot.REAL_ESTATE.value),
        (Category.kpi_role == "liquid_savings", Pot.LIQUID_SAVINGS.value),
        (Transaction.savings_bundle_id.isnot(None), Pot.LIQUID_SAVINGS.value),
        (
            or_(
                Category.is_savings_category.is_(True),
                Transaction.is_savings_related.is_(True),
            ),
            Pot.LIQUID_SAVINGS.value,
        ),
        (Category.kpi_role == "investment", Pot.INVESTMENT.value),  # PRIMARY
        (Category.name == INVESTMENT_CATEGORY_NAME, Pot.INVESTMENT.value),  # SECONDARY fallback
        (Transaction.project_id.isnot(None), Pot.PROJECT.value),
        else_=Pot.EXTERNAL.value,
    )


def external_income(db: Session) -> float:
    """Sum of income transactions classified as EXTERNAL (real external income)."""
    pot_expr = _pot_case_expr()
    total = (
        db.query(func.coalesce(func.sum(Transaction.amount), 0))
        .join(Category, Transaction.category_id == Category.id)
        .filter(
            Transaction.deleted_at.is_(None),
            Transaction.type == TransactionType.INCOME,
            pot_expr == Pot.EXTERNAL.value,
        )
        .scalar()
    )
    return float(total or 0)


def external_expense(db: Session) -> float:
    """Sum of expense transactions classified as EXTERNAL (real consumption)."""
    pot_expr = _pot_case_expr()
    total = (
        db.query(func.coalesce(func.sum(Transaction.amount), 0))
        .join(Category, Transaction.category_id == Category.id)
        .filter(
            Transaction.deleted_at.is_(None),
            Transaction.type == TransactionType.EXPENSE,
            pot_expr == Pot.EXTERNAL.value,
        )
        .scalar()
    )
    return float(total or 0)


def net_family_surplus(db: Session) -> float:
    """external_income − external_expense — the "who owes whom" number.

    Positive means the family's true (transfer-excluded) income exceeded its
    true consumption. Unlike `liquid_cash`, this is unaffected by internal
    transfers (savings deposits, RE/investment purchases, project payments).
    """
    return external_income(db) - external_expense(db)


def liquid_cash(db: Session) -> float:
    """Raw Σincome − Σexpense across ALL transactions (every pot included).

    This is the true spendable balance — it also matches
    dashboard_service.get_cash_on_hand()'s existing formula exactly, which is
    why `get_cash_on_hand` below is a thin alias for this function.
    """
    row = db.query(
        func.coalesce(
            func.sum(
                case(
                    (
                        and_(Transaction.type == TransactionType.INCOME, Transaction.deleted_at.is_(None)),
                        Transaction.amount,
                    ),
                    else_=0,
                )
            ),
            0,
        ).label("inc"),
        func.coalesce(
            func.sum(
                case(
                    (
                        and_(Transaction.type == TransactionType.EXPENSE, Transaction.deleted_at.is_(None)),
                        Transaction.amount,
                    ),
                    else_=0,
                )
            ),
            0,
        ).label("exp"),
    ).first()
    return float(row.inc or 0) - float(row.exp or 0)


# Phase-1 migration aid: existing callers of dashboard_service.get_cash_on_hand
# keep working unmodified; new/refactored callers (Phase 2+) can import this
# instead. Same formula, same result — see `liquid_cash`.
get_cash_on_hand = liquid_cash


def pot_balance(db: Session, pot: Pot) -> float:
    """Net amount transferred INTO *pot* to date: Σ(expense) − Σ(income) for
    transactions classified into that pot (deposits minus withdrawals/returns).

    Only defined for internal pots — EXTERNAL has no "balance" (it's not a
    pot the family owns money in).
    """
    if pot is Pot.EXTERNAL:
        raise ValueError("pot_balance() is only defined for internal pots, not EXTERNAL")

    pot_expr = _pot_case_expr()
    row = (
        db.query(
            func.coalesce(
                func.sum(case((Transaction.type == TransactionType.EXPENSE, Transaction.amount), else_=0)), 0
            ).label("out_"),
            func.coalesce(
                func.sum(case((Transaction.type == TransactionType.INCOME, Transaction.amount), else_=0)), 0
            ).label("in_"),
        )
        .join(Category, Transaction.category_id == Category.id)
        .filter(Transaction.deleted_at.is_(None), pot_expr == pot.value)
        .first()
    )
    return float(row.out_ or 0) - float(row.in_ or 0)


def net_worth(db: Session) -> float:
    """liquid_cash + total_savings + total_assets_current + total_projects_paid.

    Phase 4 correction (savings component): the savings term is now
    `SavingsBundle.current_amount` (actual current balance) instead of
    `future_amount` (projected maturity value, which folded in ~21M of
    UNEARNED interest). Same filter as before: status ACTIVE, deleted_at IS
    NULL. This lowers net_worth by the unearned-interest delta versus Phase 1.

    Still deferred (later): dedicated REAL_ESTATE / INVESTMENT stock sources —
    those pots have no net_worth stock term yet, only transaction-derived
    pot_balance flows.
    """
    cash = liquid_cash(db)

    total_savings = float(
        db.query(func.coalesce(func.sum(SavingsBundle.current_amount), 0))
        .filter(SavingsBundle.status == SavingsStatus.ACTIVE, SavingsBundle.deleted_at.is_(None))
        .scalar()
        or 0
    )
    total_assets_current = float(db.query(func.coalesce(func.sum(OtherAsset.current_value_vnd), 0)).scalar() or 0)
    total_projects_paid = float(
        db.query(func.coalesce(func.sum(ProjectPayment.amount), 0))
        .filter(ProjectPayment.status == PaymentStatus.PAID)
        .scalar()
        or 0
    )

    return cash + total_savings + total_assets_current + total_projects_paid
