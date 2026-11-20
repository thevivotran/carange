"""Rule suggestions: descriptions repeatedly filed under one category without a rule."""

from datetime import date, timedelta

import pytest

from app.models.database import Category, Transaction, TransactionRule, TransactionType
from app.services.rules_service import normalise_description, suggest_rules

TODAY = date(2026, 11, 20)


@pytest.fixture()
def cats(db_session):
    transport = Category(name="Transport", type=TransactionType.EXPENSE, color="#111111", icon="car")
    food = Category(name="Food", type=TransactionType.EXPENSE, color="#222222", icon="food")
    db_session.add_all([transport, food])
    db_session.commit()
    return transport, food


def _add(db, cat, desc, n, days_ago=5):
    for i in range(n):
        db.add(
            Transaction(
                date=TODAY - timedelta(days=days_ago),
                amount=50_000 + i,
                type=TransactionType.EXPENSE,
                category_id=cat.id,
                description=f"{desc} #{1000 + i}",
            )
        )
    db.commit()


def test_normalise_strips_digits_and_leftover_punctuation():
    assert normalise_description("GRAB*Ride 12/11 #4821") == "grab*ride"
    assert normalise_description("Circle-K 24/7") == "circle-k"
    assert normalise_description(None) == ""


def test_suggests_repeated_consistent_description(db_session, cats):
    transport, _ = cats
    _add(db_session, transport, "Grab ride", 5)
    out = suggest_rules(db_session, today=TODAY)
    assert out == [{"pattern": "grab ride", "category_id": transport.id, "count": 5, "category_name": "Transport"}]


def test_below_threshold_mixed_categories_and_old_ones_not_suggested(db_session, cats):
    transport, food = cats
    _add(db_session, transport, "Grab ride", 4)  # one short
    _add(db_session, transport, "Shopee", 5)
    _add(db_session, food, "Shopee", 2)  # 5/7 = 71% purity
    _add(db_session, food, "Old cafe", 6, days_ago=400)  # outside the lookback window
    assert suggest_rules(db_session, today=TODAY) == []


def test_existing_rule_suppresses_suggestion(db_session, cats):
    transport, _ = cats
    _add(db_session, transport, "Grab ride", 6)
    db_session.add(
        TransactionRule(
            name="grab",
            match_field="description",
            match_op="contains",
            match_value="grab",
            action_json={"set_category_id": transport.id},
        )
    )
    db_session.commit()
    assert suggest_rules(db_session, today=TODAY) == []


def test_suggestions_endpoint(client):
    r = client.get("/api/rules/suggestions")
    assert r.status_code == 200 and r.json() == []


def test_rules_page_has_suggestions_card(client):
    html = client.get("/rules").text
    assert 'id="rule-suggestions"' in html and "loadRuleSuggestions()" in html
