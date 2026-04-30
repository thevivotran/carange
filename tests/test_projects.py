"""Tests for project CRUD, milestones, and transaction drill-down."""
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


# ── Milestones (#1) ───────────────────────────────────────────────────────────

def test_list_milestones_empty(client, project_id):
    r = client.get(f"/api/projects/{project_id}/milestones")
    assert r.status_code == 200
    assert r.json() == []


def test_add_milestone(client, project_id):
    r = client.post(f"/api/projects/{project_id}/milestones", json={
        "name": "50% funded",
        "target_amount": 250_000_000,
        "is_completed": False,
        "project_id": project_id,
    })
    assert r.status_code == 200
    d = r.json()
    assert d["name"] == "50% funded"
    assert d["target_amount"] == pytest.approx(250_000_000)
    assert d["is_completed"] is False


def test_multiple_milestones_ordered_by_amount(client, project_id):
    client.post(f"/api/projects/{project_id}/milestones", json={
        "name": "75%", "target_amount": 375_000_000, "is_completed": False, "project_id": project_id
    })
    client.post(f"/api/projects/{project_id}/milestones", json={
        "name": "25%", "target_amount": 125_000_000, "is_completed": False, "project_id": project_id
    })
    milestones = client.get(f"/api/projects/{project_id}/milestones").json()
    assert len(milestones) == 2
    assert milestones[0]["target_amount"] < milestones[1]["target_amount"]


def test_complete_milestone(client, project_id):
    ms_id = client.post(f"/api/projects/{project_id}/milestones", json={
        "name": "Goal", "target_amount": 100_000_000, "is_completed": False, "project_id": project_id
    }).json()["id"]
    r = client.patch(f"/api/projects/{project_id}/milestones/{ms_id}/complete")
    assert r.status_code == 200
    milestones = client.get(f"/api/projects/{project_id}/milestones").json()
    completed = next(m for m in milestones if m["id"] == ms_id)
    assert completed["is_completed"] is True
    assert completed["completed_at"] is not None


def test_delete_milestone(client, project_id):
    ms_id = client.post(f"/api/projects/{project_id}/milestones", json={
        "name": "ToDelete", "target_amount": 50_000_000, "is_completed": False, "project_id": project_id
    }).json()["id"]
    r = client.delete(f"/api/projects/{project_id}/milestones/{ms_id}")
    assert r.status_code == 200
    remaining = [m["id"] for m in client.get(f"/api/projects/{project_id}/milestones").json()]
    assert ms_id not in remaining


def test_delete_nonexistent_milestone_returns_404(client, project_id):
    assert client.delete(f"/api/projects/{project_id}/milestones/999999").status_code == 404


def test_delete_milestone_wrong_project_returns_404(client, project_id):
    """Deleting a milestone from the wrong project should 404."""
    ms_id = client.post(f"/api/projects/{project_id}/milestones", json={
        "name": "Mine", "target_amount": 1_000_000, "is_completed": False, "project_id": project_id
    }).json()["id"]
    other_id = client.post("/api/projects/", json=_project_payload(name="Other")).json()["id"]
    r = client.delete(f"/api/projects/{other_id}/milestones/{ms_id}")
    assert r.status_code == 404


def test_complete_milestone_for_wrong_project_returns_404(client, project_id):
    ms_id = client.post(f"/api/projects/{project_id}/milestones", json={
        "name": "Mine", "target_amount": 1_000_000, "is_completed": False, "project_id": project_id
    }).json()["id"]
    other_id = client.post("/api/projects/", json=_project_payload(name="Other2")).json()["id"]
    r = client.patch(f"/api/projects/{other_id}/milestones/{ms_id}/complete")
    assert r.status_code == 404


# ── Project transactions drill-down (#9 / filter #3) ─────────────────────────

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
