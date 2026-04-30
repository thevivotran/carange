"""CRUD and filter tests for transactions endpoint."""
import pytest
from datetime import date


@pytest.fixture()
def cat_ids(client):
    """Create one income and one expense category, return their IDs."""
    inc = client.post("/api/categories/", json={"name": "Salary", "type": "income", "color": "#green", "icon": "x"}).json()["id"]
    exp = client.post("/api/categories/", json={"name": "Food", "type": "expense", "color": "#red", "icon": "x"}).json()["id"]
    return {"income": inc, "expense": exp}


def _make_tx(client, *, date_str, amount, type_, category_id, description="", is_savings_related=False):
    return client.post("/api/transactions/", json={
        "date": date_str,
        "amount": amount,
        "type": type_,
        "category_id": category_id,
        "description": description,
        "payment_method": "cash",
        "is_savings_related": is_savings_related,
    })


# ── Create ────────────────────────────────────────────────────────────────────

def test_create_income_transaction(client, cat_ids):
    r = _make_tx(client, date_str="2026-04-01", amount=5_000_000,
                 type_="income", category_id=cat_ids["income"])
    assert r.status_code == 200
    d = r.json()
    assert d["amount"] == 5_000_000
    assert d["type"] == "income"
    assert d["id"] > 0


def test_create_expense_transaction(client, cat_ids):
    r = _make_tx(client, date_str="2026-04-02", amount=300_000,
                 type_="expense", category_id=cat_ids["expense"])
    assert r.status_code == 200
    assert r.json()["type"] == "expense"


def test_create_transaction_zero_amount_rejected(client, cat_ids):
    r = _make_tx(client, date_str="2026-04-01", amount=0,
                 type_="income", category_id=cat_ids["income"])
    assert r.status_code == 422


def test_create_transaction_negative_amount_rejected(client, cat_ids):
    r = _make_tx(client, date_str="2026-04-01", amount=-500,
                 type_="income", category_id=cat_ids["income"])
    assert r.status_code == 422


# ── Read ──────────────────────────────────────────────────────────────────────

def test_list_transactions(client, cat_ids):
    _make_tx(client, date_str="2026-04-01", amount=1_000, type_="income", category_id=cat_ids["income"])
    _make_tx(client, date_str="2026-04-02", amount=2_000, type_="expense", category_id=cat_ids["expense"])
    r = client.get("/api/transactions/")
    assert r.status_code == 200
    assert len(r.json()) >= 2


def test_get_single_transaction(client, cat_ids):
    tx_id = _make_tx(client, date_str="2026-04-05", amount=9_000,
                     type_="income", category_id=cat_ids["income"]).json()["id"]
    r = client.get(f"/api/transactions/{tx_id}")
    assert r.status_code == 200
    assert r.json()["amount"] == 9_000


def test_get_nonexistent_transaction_returns_404(client):
    r = client.get("/api/transactions/999999")
    assert r.status_code == 404


# ── Filter ────────────────────────────────────────────────────────────────────

def test_filter_by_type(client, cat_ids):
    _make_tx(client, date_str="2026-04-01", amount=1_000, type_="income", category_id=cat_ids["income"])
    _make_tx(client, date_str="2026-04-02", amount=2_000, type_="expense", category_id=cat_ids["expense"])
    r = client.get("/api/transactions/?type=income")
    assert r.status_code == 200
    assert all(t["type"] == "income" for t in r.json())


def test_filter_by_date_range(client, cat_ids):
    _make_tx(client, date_str="2026-04-10", amount=1_000, type_="income", category_id=cat_ids["income"])
    _make_tx(client, date_str="2026-03-10", amount=2_000, type_="income", category_id=cat_ids["income"])
    r = client.get("/api/transactions/?start_date=2026-04-01&end_date=2026-04-30")
    assert r.status_code == 200
    dates = [t["date"] for t in r.json()]
    assert all(d.startswith("2026-04") for d in dates)


# ── Update ────────────────────────────────────────────────────────────────────

def test_update_transaction_amount(client, cat_ids):
    tx_id = _make_tx(client, date_str="2026-04-01", amount=1_000,
                     type_="income", category_id=cat_ids["income"]).json()["id"]
    r = client.put(f"/api/transactions/{tx_id}", json={"amount": 9_999})
    assert r.status_code == 200
    assert r.json()["amount"] == 9_999


def test_update_transaction_date(client, cat_ids):
    tx_id = _make_tx(client, date_str="2026-04-01", amount=500,
                     type_="expense", category_id=cat_ids["expense"]).json()["id"]
    r = client.put(f"/api/transactions/{tx_id}", json={"date": "2026-04-15"})
    assert r.status_code == 200
    assert r.json()["date"] == "2026-04-15"


# ── Delete ────────────────────────────────────────────────────────────────────

def test_delete_transaction(client, cat_ids):
    tx_id = _make_tx(client, date_str="2026-04-01", amount=500,
                     type_="expense", category_id=cat_ids["expense"]).json()["id"]
    r = client.delete(f"/api/transactions/{tx_id}")
    assert r.status_code == 200
    assert client.get(f"/api/transactions/{tx_id}").status_code == 404


def test_delete_nonexistent_transaction_returns_404(client):
    assert client.delete("/api/transactions/999999").status_code == 404


def test_update_transaction_with_nonexistent_category_returns_404(client, cat_ids):
    tx_id = _make_tx(client, date_str="2026-04-01", amount=500,
                     type_="expense", category_id=cat_ids["expense"]).json()["id"]
    r = client.put(f"/api/transactions/{tx_id}", json={"category_id": 999999})
    assert r.status_code == 404


# ── Keyword search ───────────────────────────────────────────────────────────

def test_search_by_description(client, cat_ids):
    _make_tx(client, date_str="2026-04-01", amount=500, type_="expense",
             category_id=cat_ids["expense"], description="Coffee at Highlands")
    _make_tx(client, date_str="2026-04-02", amount=200_000, type_="expense",
             category_id=cat_ids["expense"], description="Grab taxi")
    r = client.get("/api/transactions/?search=coffee")
    assert r.status_code == 200
    results = r.json()
    assert len(results) == 1
    assert "Coffee" in results[0]["description"]
