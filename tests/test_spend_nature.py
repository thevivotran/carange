"""Tests for the category-level spend_nature classification."""

from app.models.database import Category, SpendNature, TransactionType


def test_default_spend_nature_is_mixed(db_session):
    cat = Category(name="Food", type=TransactionType.EXPENSE)
    db_session.add(cat)
    db_session.commit()
    db_session.refresh(cat)

    assert cat.spend_nature == SpendNature.MIXED


class TestCategorySpendNatureEndpoint:
    def test_create_category_with_recurring_nature(self, client):
        r = client.post(
            "/api/categories/",
            json={"name": "Household", "type": "expense", "spend_nature": "recurring"},
        )
        assert r.status_code == 200
        assert r.json()["spend_nature"] == "recurring"

    def test_create_category_defaults_to_mixed(self, client):
        r = client.post("/api/categories/", json={"name": "Misc", "type": "expense"})
        assert r.status_code == 200
        assert r.json()["spend_nature"] == "mixed"

    def test_update_spend_nature_to_discretionary(self, client, db_session):
        cat = Category(name="Electronics", type=TransactionType.EXPENSE)
        db_session.add(cat)
        db_session.commit()

        r = client.put(
            f"/api/categories/{cat.id}",
            json={"name": "Electronics", "type": "expense", "spend_nature": "discretionary"},
        )
        assert r.status_code == 200

        db_session.refresh(cat)
        assert cat.spend_nature == SpendNature.DISCRETIONARY

    def test_invalid_spend_nature_value_is_rejected(self, client):
        r = client.post(
            "/api/categories/",
            json={"name": "Bogus", "type": "expense", "spend_nature": "not_a_real_value"},
        )
        assert r.status_code == 422
