"""Tests for FinancialProject soft-delete, restore, and hard-delete."""

import pytest
from app.models.database import FinancialProject


def _project_payload(name="Test Project"):
    return {"name": name, "type": "custom", "priority": "low"}


@pytest.fixture()
def project_id(client):
    r = client.post("/api/projects/", json=_project_payload())
    assert r.status_code == 200
    return r.json()["id"]


def test_project_list_excludes_deleted(client, project_id):
    client.delete(f"/api/projects/{project_id}")
    r = client.get("/api/projects/")
    ids = [p["id"] for p in r.json()]
    assert project_id not in ids


def test_get_deleted_project_returns_404(client, project_id):
    client.delete(f"/api/projects/{project_id}")
    assert client.get(f"/api/projects/{project_id}").status_code == 404


def test_deleted_project_appears_in_trash(client, project_id):
    client.delete(f"/api/projects/{project_id}")
    trash = client.get("/api/projects/trash").json()
    ids = [p["id"] for p in trash]
    assert project_id in ids


def test_restore_project(client, project_id):
    client.delete(f"/api/projects/{project_id}")
    r = client.post(f"/api/projects/{project_id}/restore")
    assert r.status_code == 200
    assert client.get(f"/api/projects/{project_id}").status_code == 200


def test_restored_project_not_in_trash(client, project_id):
    client.delete(f"/api/projects/{project_id}")
    client.post(f"/api/projects/{project_id}/restore")
    trash = client.get("/api/projects/trash").json()
    ids = [p["id"] for p in trash]
    assert project_id not in ids


def test_hard_delete_project(client, project_id, db_session):
    client.delete(f"/api/projects/{project_id}")
    r = client.delete(f"/api/projects/{project_id}/hard")
    assert r.status_code == 200
    assert db_session.query(FinancialProject).filter(FinancialProject.id == project_id).first() is None


def test_hard_delete_non_trashed_project_returns_404(client, project_id):
    r = client.delete(f"/api/projects/{project_id}/hard")
    assert r.status_code == 404


def test_restore_non_trashed_project_returns_404(client, project_id):
    r = client.post(f"/api/projects/{project_id}/restore")
    assert r.status_code == 404


def test_stats_excludes_deleted(client, project_id):
    r_before = client.get("/api/projects/stats/summary").json()
    count_before = r_before["active_projects_count"]
    client.delete(f"/api/projects/{project_id}")
    r_after = client.get("/api/projects/stats/summary").json()
    assert r_after["active_projects_count"] == count_before - 1
