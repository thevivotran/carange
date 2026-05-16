"""Full CRUD tests for transaction templates endpoint."""

import pytest


@pytest.fixture()
def expense_cat(client):
    r = client.post(
        "/api/categories/", json={"name": "Food", "type": "expense", "color": "#EF4444", "icon": "utensils"}
    )
    assert r.status_code == 200
    return r.json()["id"]


@pytest.fixture()
def income_cat(client):
    r = client.post("/api/categories/", json={"name": "Salary", "type": "income", "color": "#10B981", "icon": "money"})
    assert r.status_code == 200
    return r.json()["id"]


def _template(client, category_id, **overrides):
    base = {
        "name": "Lunch",
        "amount": 50_000,
        "type": "expense",
        "category_id": category_id,
        "description": "Daily lunch",
        "payment_method": "cash",
    }
    base.update(overrides)
    r = client.post("/api/templates/", json=base)
    assert r.status_code == 200
    return r.json()


# ── Create ────────────────────────────────────────────────────────────────────


def test_create_template(client, expense_cat):
    r = client.post(
        "/api/templates/",
        json={"name": "Coffee", "amount": 30_000, "type": "expense", "category_id": expense_cat},
    )
    assert r.status_code == 200
    d = r.json()
    assert d["name"] == "Coffee"
    assert d["amount"] == pytest.approx(30_000)
    assert d["is_active"] is True
    assert d["id"] > 0


def test_create_template_nonexistent_category_returns_404(client):
    r = client.post(
        "/api/templates/",
        json={"name": "Ghost", "amount": 10_000, "type": "expense", "category_id": 999999},
    )
    assert r.status_code == 404


# ── Read ──────────────────────────────────────────────────────────────────────


def test_list_templates(client, expense_cat):
    _template(client, expense_cat, name="T1")
    _template(client, expense_cat, name="T2")
    r = client.get("/api/templates/")
    assert r.status_code == 200
    names = [t["name"] for t in r.json()]
    assert "T1" in names and "T2" in names


def test_list_templates_filter_by_type(client, expense_cat, income_cat):
    _template(client, expense_cat, name="ExpTpl", type="expense")
    _template(client, income_cat, name="IncTpl", type="income")
    r = client.get("/api/templates/?type=expense")
    assert r.status_code == 200
    assert all(t["type"] == "expense" for t in r.json())


def test_list_templates_filter_by_category(client, expense_cat, income_cat):
    _template(client, expense_cat, name="CatMatch")
    _template(client, income_cat, name="CatOther", type="income")
    r = client.get(f"/api/templates/?category_id={expense_cat}")
    assert r.status_code == 200
    assert all(t["category_id"] == expense_cat for t in r.json())


def test_list_templates_filter_by_is_active(client, expense_cat):
    t = _template(client, expense_cat, name="ActiveTpl")
    _template(client, expense_cat, name="InactiveTpl")
    client.put(
        f"/api/templates/{t['id']}",
        json={"is_active": False, "name": "ActiveTpl", "amount": 50_000, "type": "expense", "category_id": expense_cat},
    )
    r = client.get("/api/templates/?is_active=true")
    assert r.status_code == 200
    assert all(t["is_active"] is True for t in r.json())


def test_get_single_template(client, expense_cat):
    t = _template(client, expense_cat, name="Solo")
    r = client.get(f"/api/templates/{t['id']}")
    assert r.status_code == 200
    assert r.json()["name"] == "Solo"


def test_get_nonexistent_template_returns_404(client):
    r = client.get("/api/templates/999999")
    assert r.status_code == 404


# ── Update ────────────────────────────────────────────────────────────────────


def test_update_template_name(client, expense_cat):
    t = _template(client, expense_cat, name="Old")
    r = client.put(
        f"/api/templates/{t['id']}",
        json={"name": "New", "amount": 50_000, "type": "expense", "category_id": expense_cat},
    )
    assert r.status_code == 200
    assert r.json()["name"] == "New"


def test_update_template_nonexistent_returns_404(client):
    r = client.put(
        "/api/templates/999999",
        json={"name": "Ghost", "amount": 1_000, "type": "expense", "category_id": 1},
    )
    assert r.status_code == 404


def test_update_template_with_nonexistent_category_returns_404(client, expense_cat):
    t = _template(client, expense_cat)
    r = client.put(
        f"/api/templates/{t['id']}",
        json={"name": "X", "amount": 50_000, "type": "expense", "category_id": 999999},
    )
    assert r.status_code == 404


# ── Delete ────────────────────────────────────────────────────────────────────


def test_delete_template(client, expense_cat):
    t = _template(client, expense_cat, name="ToDelete")
    r = client.delete(f"/api/templates/{t['id']}")
    assert r.status_code == 200
    assert client.get(f"/api/templates/{t['id']}").status_code == 404


def test_delete_nonexistent_template_returns_404(client):
    r = client.delete("/api/templates/999999")
    assert r.status_code == 404
