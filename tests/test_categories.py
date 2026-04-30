"""CRUD tests for categories endpoint."""
from datetime import date


def test_create_expense_category(client):
    r = client.post("/api/categories/", json={
        "name": "Groceries", "type": "expense", "color": "#FF0000", "icon": "shopping-cart"
    })
    assert r.status_code == 200
    d = r.json()
    assert d["name"] == "Groceries"
    assert d["type"] == "expense"
    assert d["id"] > 0


def test_create_income_category(client):
    r = client.post("/api/categories/", json={
        "name": "Bonus", "type": "income", "color": "#00FF00", "icon": "gift"
    })
    assert r.status_code == 200
    assert r.json()["type"] == "income"


def test_list_categories(client):
    client.post("/api/categories/", json={"name": "A", "type": "expense", "color": "#aaa", "icon": "x"})
    client.post("/api/categories/", json={"name": "B", "type": "income", "color": "#bbb", "icon": "y"})
    r = client.get("/api/categories/")
    assert r.status_code == 200
    names = [c["name"] for c in r.json()]
    assert "A" in names and "B" in names


def test_get_single_category(client):
    cat_id = client.post("/api/categories/", json={
        "name": "Solo", "type": "expense", "color": "#123456", "icon": "circle"
    }).json()["id"]
    r = client.get(f"/api/categories/{cat_id}")
    assert r.status_code == 200
    assert r.json()["name"] == "Solo"


def test_get_nonexistent_category_returns_404(client):
    r = client.get("/api/categories/99999")
    assert r.status_code == 404


def test_update_category(client):
    cat_id = client.post("/api/categories/", json={
        "name": "Old", "type": "expense", "color": "#111111", "icon": "circle"
    }).json()["id"]
    r = client.put(f"/api/categories/{cat_id}", json={
        "name": "New", "type": "expense", "color": "#222222", "icon": "circle"
    })
    assert r.status_code == 200
    assert r.json()["name"] == "New"


def test_delete_category(client):
    cat_id = client.post("/api/categories/", json={
        "name": "ToDelete", "type": "expense", "color": "#333333", "icon": "circle"
    }).json()["id"]
    r = client.delete(f"/api/categories/{cat_id}")
    assert r.status_code == 200
    assert client.get(f"/api/categories/{cat_id}").status_code == 404


def test_delete_nonexistent_category_returns_404(client):
    r = client.delete("/api/categories/99999")
    assert r.status_code == 404
