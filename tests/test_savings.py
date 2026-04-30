"""Tests for savings bundles: CRUD, mark-completed auto-transaction, rollover."""
import pytest
from datetime import date


@pytest.fixture()
def investment_cat(client):
    return client.post("/api/categories/", json={
        "name": "Investment", "type": "income", "color": "#3B82F6", "icon": "chart"
    }).json()


def _bundle_payload(**overrides):
    base = {
        "name": "Test Bundle", "bank_name": "VCB",
        "type": "fixed_deposit",
        "initial_deposit": 50_000_000,
        "current_amount": 50_000_000,
        "future_amount": 53_000_000,
        "interest_rate": 6.0,
        "start_date": "2026-01-01",
        "maturity_date": "2026-07-01",
    }
    base.update(overrides)
    return base


# ── CRUD ──────────────────────────────────────────────────────────────────────

def test_create_savings_bundle(client):
    r = client.post("/api/savings/", json=_bundle_payload())
    assert r.status_code == 200
    d = r.json()
    assert d["name"] == "Test Bundle"
    assert d["status"] == "active"
    assert d["initial_deposit"] == 50_000_000
    assert d["future_amount"] == 53_000_000


def test_create_bundle_zero_initial_deposit_rejected(client):
    r = client.post("/api/savings/", json=_bundle_payload(initial_deposit=0))
    assert r.status_code == 422


def test_list_savings_bundles(client):
    client.post("/api/savings/", json=_bundle_payload(name="A"))
    client.post("/api/savings/", json=_bundle_payload(name="B"))
    r = client.get("/api/savings/")
    assert r.status_code == 200
    names = [b["name"] for b in r.json()]
    assert "A" in names and "B" in names


def test_get_single_bundle(client):
    bundle_id = client.post("/api/savings/", json=_bundle_payload()).json()["id"]
    r = client.get(f"/api/savings/{bundle_id}")
    assert r.status_code == 200
    assert r.json()["id"] == bundle_id


def test_get_nonexistent_bundle_returns_404(client):
    assert client.get("/api/savings/999999").status_code == 404


def test_update_bundle_amount(client):
    bundle_id = client.post("/api/savings/", json=_bundle_payload()).json()["id"]
    r = client.put(f"/api/savings/{bundle_id}", json={"future_amount": 55_000_000})
    assert r.status_code == 200
    assert r.json()["future_amount"] == 55_000_000


def test_delete_bundle(client):
    bundle_id = client.post("/api/savings/", json=_bundle_payload()).json()["id"]
    r = client.delete(f"/api/savings/{bundle_id}")
    assert r.status_code == 200
    assert client.get(f"/api/savings/{bundle_id}").status_code == 404


# ── Mark completed ────────────────────────────────────────────────────────────

def test_mark_completed_changes_status(client, investment_cat):
    bundle_id = client.post("/api/savings/", json=_bundle_payload()).json()["id"]
    r = client.post(f"/api/savings/{bundle_id}/mark-completed")
    assert r.status_code == 200
    bundle = client.get(f"/api/savings/{bundle_id}").json()
    assert bundle["status"] == "completed"


def test_mark_completed_creates_income_transaction(client, investment_cat, db_session):
    """Completing a bundle must auto-create an income transaction for future_amount."""
    from app.models.database import Transaction as TxModel
    bundle_id = client.post("/api/savings/", json=_bundle_payload(future_amount=53_000_000)).json()["id"]
    client.post(f"/api/savings/{bundle_id}/mark-completed")

    tx = db_session.query(TxModel).filter(
        TxModel.savings_bundle_id == bundle_id,
        TxModel.type.in_(["income"]),
    ).first()
    assert tx is not None, "Auto income transaction was not created"
    assert tx.amount == pytest.approx(53_000_000)
    assert tx.is_savings_related is True


def test_mark_completed_twice_returns_400(client, investment_cat):
    bundle_id = client.post("/api/savings/", json=_bundle_payload()).json()["id"]
    client.post(f"/api/savings/{bundle_id}/mark-completed")
    r = client.post(f"/api/savings/{bundle_id}/mark-completed")
    assert r.status_code == 400


# ── Rollover ──────────────────────────────────────────────────────────────────

def test_rollover_creates_new_active_bundle(client):
    bundle_id = client.post("/api/savings/", json=_bundle_payload(
        future_amount=53_000_000,
        start_date="2026-01-01",
        maturity_date="2026-07-01",
    )).json()["id"]
    r = client.post(f"/api/savings/{bundle_id}/rollover")
    assert r.status_code == 200
    new = r.json()
    assert new["status"] == "active"
    assert new["initial_deposit"] == pytest.approx(53_000_000)
    assert "Rollover" in new["name"]


def test_rollover_marks_original_as_completed(client, db_session):
    from app.models.database import SavingsBundle as SBModel
    bundle_id = client.post("/api/savings/", json=_bundle_payload()).json()["id"]
    client.post(f"/api/savings/{bundle_id}/rollover")
    original = db_session.get(SBModel, bundle_id)
    assert original.status.value == "completed"


# ── Unlink project (#4) ───────────────────────────────────────────────────────

def test_unlink_project_from_bundle(client):
    """Setting linked_project_id to null should remove the project association."""
    project_id = client.post("/api/projects/", json={
        "name": "House Project", "type": "real_estate", "priority": "high"
    }).json()["id"]

    bundle_id = client.post("/api/savings/", json=_bundle_payload(
        linked_project_id=project_id
    )).json()["id"]

    assert client.get(f"/api/savings/{bundle_id}").json()["linked_project_id"] == project_id

    r = client.put(f"/api/savings/{bundle_id}", json={"linked_project_id": None})
    assert r.status_code == 200
    assert r.json()["linked_project_id"] is None


def test_link_nonexistent_project_returns_404(client):
    """Linking to a non-existent project should return 404."""
    bundle_id = client.post("/api/savings/", json=_bundle_payload()).json()["id"]
    r = client.put(f"/api/savings/{bundle_id}", json={"linked_project_id": 999999})
    assert r.status_code == 404
