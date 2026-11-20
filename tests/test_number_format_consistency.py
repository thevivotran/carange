"""Every page formats money through the shared helpers and follows the Numbers & status
rules (no hand-built "₫…K", no sign on balances, no purple stat colours)."""

import re
from datetime import date
from decimal import Decimal
from pathlib import Path

from app.models.database import Transaction, TransactionType
from app.services.currency_format import format_amount_abbrev

TEMPLATES = Path(__file__).resolve().parent.parent / "app" / "templates"


def test_pulse_recent_amounts_use_shared_formatter(client, db_session, income_cat, expense_cat):
    today = date.today()
    db_session.add_all(
        [
            Transaction(date=today, amount=Decimal(352_000), type=TransactionType.EXPENSE, category_id=expense_cat.id),
            Transaction(date=today, amount=Decimal(5_000_000), type=TransactionType.INCOME, category_id=income_cat.id),
        ]
    )
    db_session.commit()
    html = client.get("/pulse").text
    assert format_amount_abbrev(-352_000, signed=True) in html
    assert format_amount_abbrev(5_000_000, signed=True) in html
    assert not re.search(r"-?₫[\d,]+K", html), "Pulse must not hand-build symbol-prefixed K amounts"


def test_no_template_hand_builds_currency_amounts():
    for path in TEMPLATES.rglob("*.html"):
        text = path.read_text()
        assert "currency_symbol() }}{{" not in text, f"{path} builds an amount by hand"


def test_no_purple_text_in_templates():
    for path in TEMPLATES.rglob("*.html"):
        for line in path.read_text().splitlines():
            if ".dark" in line and "{" in line:
                continue  # dark-mode CSS overrides, not usages
            assert not re.search(r"(?<![\w-])text-purple-\d{3}", line), f"{path}: {line.strip()[:80]}"


def test_liquid_cash_balance_has_no_plus_sign(client, db_session, income_cat):
    db_session.add(
        Transaction(
            date=date.today(), amount=Decimal(69_000_000), type=TransactionType.INCOME, category_id=income_cat.id
        )
    )
    db_session.commit()
    html = client.get("/").text
    block = html[html.index("Liquid Cash</p>") : html.index("Liquid Cash</p>") + 1200]
    assert format_amount_abbrev(69_000_000) in block
    assert format_amount_abbrev(69_000_000, signed=True) not in block
