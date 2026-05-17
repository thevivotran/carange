"""Tests for SavingsBundle soft-delete, restore, and hard-delete."""

import pytest
from app.models.database import SavingsBundle


def _bundle_payload(name="Test Bundle"):
    return {
        "name": name,
        "bank_name": "VCB",
        "type": "fixed_deposit",
        "initial_deposit": 10_000_000,
        "current_amount": 10_000_000,
        "future_amount": 10_500_000,
        "interest_rate": 5.0,
        "start_date": "2026-01-01",
        "maturity_date": "2026-07-01",
    }


@pytest.fixture()
def bundle_id(client):
    r = client.post("/api/savings/", json=_bundle_payload())
    assert r.status_code == 200
    return r.json()["id"]


def test_list_excludes_deleted(client, bundle_id):
    client.delete(f"/api/savings/{bundle_id}")
    r = client.get("/api/savings/")
    ids = [b["id"] for b in r.json()]
    assert bundle_id not in ids


def test_get_deleted_bundle_returns_404(client, bundle_id):
    client.delete(f"/api/savings/{bundle_id}")
    assert client.get(f"/api/savings/{bundle_id}").status_code == 404


def test_deleted_bundle_in_trash(client, bundle_id):
    client.delete(f"/api/savings/{bundle_id}")
    trash = client.get("/api/savings/trash").json()
    ids = [b["id"] for b in trash]
    assert bundle_id in ids


def test_restore_bundle(client, bundle_id):
    client.delete(f"/api/savings/{bundle_id}")
    r = client.post(f"/api/savings/{bundle_id}/restore")
    assert r.status_code == 200
    assert client.get(f"/api/savings/{bundle_id}").status_code == 200


def test_restored_bundle_not_in_trash(client, bundle_id):
    client.delete(f"/api/savings/{bundle_id}")
    client.post(f"/api/savings/{bundle_id}/restore")
    trash = client.get("/api/savings/trash").json()
    ids = [b["id"] for b in trash]
    assert bundle_id not in ids


def test_hard_delete_bundle(client, bundle_id, db_session):
    client.delete(f"/api/savings/{bundle_id}")
    r = client.delete(f"/api/savings/{bundle_id}/hard")
    assert r.status_code == 200
    assert db_session.query(SavingsBundle).filter(SavingsBundle.id == bundle_id).first() is None


def test_hard_delete_non_trashed_returns_404(client, bundle_id):
    r = client.delete(f"/api/savings/{bundle_id}/hard")
    assert r.status_code == 404


def test_restore_non_trashed_returns_404(client, bundle_id):
    r = client.post(f"/api/savings/{bundle_id}/restore")
    assert r.status_code == 404
