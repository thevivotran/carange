"""Tests for project CRUD and transaction drill-down."""
import pytest
from datetime import date


def _project_payload(**overrides):
    base = {"name": "Buy Apartment", "type": "real_estate", "priority": "high"}
    base.update(overrides)
    return base


@pytest.fixture()
def project_id(client):
    r = client.post("/api/projects/", json=_project_payload())
    assert r.status_code == 200
    return r.json()["id"]


@pytest.fixture()
def expense_category(client):
    r = client.post("/api/categories/", json={"name": "BDS", "type": "expense", "color": "#8B5CF6", "icon": "home"})
    assert r.status_code == 200
    return r.json()["id"]


# ── Project CRUD ──────────────────────────────────────────────────────────────

def test_create_project(client):
    r = client.post("/api/projects/", json=_project_payload())
    assert r.status_code == 200
    d = r.json()
    assert d["name"] == "Buy Apartment"
    assert d["status"] == "planning"
    assert d["id"] > 0


def test_list_projects(client):
    client.post("/api/projects/", json=_project_payload(name="P1"))
    client.post("/api/projects/", json=_project_payload(name="P2"))
    r = client.get("/api/projects/")
    assert r.status_code == 200
    names = [p["name"] for p in r.json()]
    assert "P1" in names and "P2" in names


def test_get_single_project(client, project_id):
    r = client.get(f"/api/projects/{project_id}")
    assert r.status_code == 200
    assert r.json()["id"] == project_id


def test_get_nonexistent_project_returns_404(client):
    assert client.get("/api/projects/999999").status_code == 404


def test_delete_project(client, project_id):
    r = client.delete(f"/api/projects/{project_id}")
    assert r.status_code == 200
    assert client.get(f"/api/projects/{project_id}").status_code == 404


# ── Project transactions drill-down ──────────────────────────────────────────

def test_filter_transactions_by_project_id(client, project_id, expense_category, db_session):
    """GET /api/transactions/?project_id must return only that project's transactions."""
    from app.models.database import Transaction, TransactionType
    tx_linked = Transaction(
        date=date(2026, 4, 15),
        amount=5_000_000,
        type=TransactionType.EXPENSE,
        category_id=expense_category,
        project_id=project_id,
    )
    tx_other = Transaction(
        date=date(2026, 4, 16),
        amount=3_000_000,
        type=TransactionType.EXPENSE,
        category_id=expense_category,
        project_id=None,
    )
    db_session.add_all([tx_linked, tx_other])
    db_session.commit()

    r = client.get(f"/api/transactions/?project_id={project_id}")
    assert r.status_code == 200
    results = r.json()
    assert len(results) == 1
    assert results[0]["amount"] == pytest.approx(5_000_000)


def test_filter_by_nonexistent_project_returns_empty(client):
    r = client.get("/api/transactions/?project_id=999999")
    assert r.status_code == 200
    assert r.json() == []


def test_transactions_without_project_filter_unaffected(client, project_id, expense_category, db_session):
    """When no project_id filter is set, all transactions are returned regardless of project linkage."""
    from app.models.database import Transaction, TransactionType
    tx1 = Transaction(date=date(2026, 4, 1), amount=1_000_000, type=TransactionType.EXPENSE,
                      category_id=expense_category, project_id=project_id)
    tx2 = Transaction(date=date(2026, 4, 2), amount=2_000_000, type=TransactionType.EXPENSE,
                      category_id=expense_category, project_id=None)
    db_session.add_all([tx1, tx2])
    db_session.commit()

    r = client.get("/api/transactions/")
    assert r.status_code == 200
    assert len(r.json()) >= 2
