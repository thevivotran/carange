import random
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.database import Category, SavingsBundle, SavingsStatus, SavingsType, Transaction, TransactionType
from app.services.settings_service import get_setting, set_setting

SAMPLE_SOURCE = "sample"
SAMPLE_BUNDLE_NAME = "Emergency Fund (Sample)"

_SAMPLE_DAYS = 60
_SALARY_AMOUNT = 25_000_000
_SALARY_HINTS = ("salary", "lương", "wage", "income")
_EXPENSE_AMOUNT_RANGE = (20_000, 500_000)  # both bounds multiples of 1000
_DAILY_EXPENSE_COUNT = (1, 3)
_DESCRIPTION_SUFFIXES = ["purchase", "payment", "expense", "spend"]


def has_sample_data(db: Session) -> bool:
    return get_setting(db, "sample_data_loaded", "false") == "true"


def _make_txn(day: date, amount: int, txn_type: TransactionType, category: Category, description: str) -> Transaction:
    return Transaction(
        date=day,
        amount=Decimal(amount),
        type=txn_type,
        category_id=category.id,
        description=description,
        payment_method="cash",
        source=SAMPLE_SOURCE,
    )


def load_sample_data(db: Session) -> int:
    """Seed ~2 months of synthetic transactions plus a sample savings goal so a new
    user can see a populated dashboard without entering anything by hand. Works against
    whatever active categories already exist (seeded defaults or a self-hoster's own),
    so it doesn't depend on specific category names or languages. Idempotent — a no-op
    if sample data is already loaded. Every record is tagged so it can be fully removed
    later via remove_sample_data()."""
    if has_sample_data(db):
        return 0

    expense_categories = (
        db.query(Category).filter(Category.type == TransactionType.EXPENSE, Category.is_active.is_(True)).all()
    )
    income_categories = (
        db.query(Category).filter(Category.type == TransactionType.INCOME, Category.is_active.is_(True)).all()
    )
    if not expense_categories or not income_categories:
        return 0

    salary_category = next(
        (c for c in income_categories if any(hint in c.name.lower() for hint in _SALARY_HINTS)),
        income_categories[0],
    )

    today = date.today()
    start = today - timedelta(days=_SAMPLE_DAYS)
    rng = random.Random(today.toordinal())  # stable for a given day, varies day to day

    created: list[Transaction] = []

    # Salary on the 1st and 15th of each covered month
    month_cursor = start.replace(day=1)
    while month_cursor <= today:
        for pay_day_num in (1, 15):
            payday = month_cursor.replace(day=pay_day_num)
            if start <= payday <= today:
                created.append(
                    _make_txn(payday, _SALARY_AMOUNT, TransactionType.INCOME, salary_category, "Monthly salary")
                )
        month_cursor = (month_cursor.replace(day=28) + timedelta(days=4)).replace(day=1)

    # A handful of expenses per day, spread across whatever expense categories exist
    lo, hi = _EXPENSE_AMOUNT_RANGE
    cursor = start
    while cursor <= today:
        daily_count = min(rng.randint(*_DAILY_EXPENSE_COUNT), len(expense_categories))
        for category in rng.sample(expense_categories, k=daily_count):
            amount = rng.randrange(lo, hi, 1000)
            description = f"{category.name} {rng.choice(_DESCRIPTION_SUFFIXES)}"
            created.append(_make_txn(cursor, amount, TransactionType.EXPENSE, category, description))
        cursor += timedelta(days=1)

    db.add_all(created)
    db.flush()

    bundle = SavingsBundle(
        name=SAMPLE_BUNDLE_NAME,
        bank_name="Sample Bank",
        type=SavingsType.SAVINGS_GOAL,
        initial_deposit=Decimal(20_000_000),
        current_amount=Decimal(35_000_000),
        future_amount=Decimal(100_000_000),
        interest_rate=4.5,
        start_date=start,
        status=SavingsStatus.ACTIVE,
        notes="Sample data — safe to remove anytime from Settings → Sample Data.",
    )
    db.add(bundle)
    db.flush()

    set_setting(db, "sample_txn_ids", ",".join(str(t.id) for t in created))
    set_setting(db, "sample_bundle_ids", str(bundle.id))
    set_setting(db, "sample_data_loaded", "true")
    return len(created) + 1


def remove_sample_data(db: Session) -> int:
    """Hard-delete every record load_sample_data created (synthetic — no trash needed)
    and clear the markers. Real user data is untouched."""
    txn_ids = [int(x) for x in get_setting(db, "sample_txn_ids", "").split(",") if x]
    bundle_ids = [int(x) for x in get_setting(db, "sample_bundle_ids", "").split(",") if x]

    removed = 0
    if txn_ids:
        removed += db.query(Transaction).filter(Transaction.id.in_(txn_ids)).delete(synchronize_session=False)
    if bundle_ids:
        removed += db.query(SavingsBundle).filter(SavingsBundle.id.in_(bundle_ids)).delete(synchronize_session=False)

    set_setting(db, "sample_data_loaded", "false")
    set_setting(db, "sample_txn_ids", "")
    set_setting(db, "sample_bundle_ids", "")
    return removed
