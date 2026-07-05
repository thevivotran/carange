"""Tests for income source CRUD, nested compensation events, and the review-overdue flag."""

from datetime import date, timedelta

import pytest


def _income_payload(**overrides):
    base = {"user_id": 1, "income_type": "salary", "employer": "Acme Corp", "role": "Engineer"}
    base.update(overrides)
    return base


def _event_payload(**overrides):
    base = {"event_date": "2026-01-15", "event_type": "raise"}
    base.update(overrides)
    return base


@pytest.fixture()
def income_source_id(client, profile_row):
    r = client.post("/api/income/", json=_income_payload())
    assert r.status_code == 200
    return r.json()["id"]


# ── Income Source CRUD ────────────────────────────────────────────────────────


def test_create_income_source(client, profile_row):
    r = client.post("/api/income/", json=_income_payload())
    assert r.status_code == 200
    d = r.json()
    assert d["employer"] == "Acme Corp"
    assert d["income_type"] == "salary"
    assert d["is_active"] is True
    assert d["id"] > 0


def test_list_income_sources(client, profile_row):
    client.post("/api/income/", json=_income_payload(employer="E1"))
    client.post("/api/income/", json=_income_payload(employer="E2"))
    r = client.get("/api/income/")
    assert r.status_code == 200
    employers = [s["employer"] for s in r.json()]
    assert "E1" in employers and "E2" in employers


def test_list_income_sources_filter_by_user_id(client, db_session):
    from app.models.database import User

    u1 = User(id=1, name="Alice", color="#2563EB")
    u2 = User(id=2, name="Bob", color="#F97316")
    db_session.add_all([u1, u2])
    db_session.commit()

    client.post("/api/income/", json=_income_payload(user_id=1, employer="AliceCo"))
    client.post("/api/income/", json=_income_payload(user_id=2, employer="BobCo"))

    r = client.get("/api/income/?user_id=1")
    assert r.status_code == 200
    employers = [s["employer"] for s in r.json()]
    assert employers == ["AliceCo"]


def test_get_single_income_source(client, income_source_id):
    r = client.get(f"/api/income/{income_source_id}")
    assert r.status_code == 200
    assert r.json()["id"] == income_source_id


def test_get_nonexistent_income_source_returns_404(client):
    assert client.get("/api/income/999999").status_code == 404


def test_update_income_source(client, income_source_id):
    r = client.put(f"/api/income/{income_source_id}", json={"employer": "New Corp", "is_active": False})
    assert r.status_code == 200
    d = r.json()
    assert d["employer"] == "New Corp"
    assert d["is_active"] is False


def test_update_nonexistent_income_source_returns_404(client):
    r = client.put("/api/income/999999", json={"employer": "New Corp"})
    assert r.status_code == 404


def test_delete_income_source(client, income_source_id):
    r = client.delete(f"/api/income/{income_source_id}")
    assert r.status_code == 200
    assert client.get(f"/api/income/{income_source_id}").status_code == 404


def test_delete_nonexistent_income_source_returns_404(client):
    assert client.delete("/api/income/999999").status_code == 404


def test_create_income_source_invalid_enum_returns_422(client, profile_row):
    r = client.post("/api/income/", json=_income_payload(income_type="not_a_real_type"))
    assert r.status_code == 422


def test_create_income_source_missing_required_field_returns_422(client, profile_row):
    r = client.post("/api/income/", json={"employer": "Acme Corp"})
    assert r.status_code == 422


# ── Compensation Event CRUD ───────────────────────────────────────────────────


def test_create_event(client, income_source_id):
    r = client.post(f"/api/income/{income_source_id}/events", json=_event_payload(amount_delta=2_000_000))
    assert r.status_code == 200
    d = r.json()
    assert d["event_type"] == "raise"
    assert d["income_source_id"] == income_source_id


def test_create_event_updates_base_amount_when_new_base_amount_given(client, income_source_id):
    r = client.post(
        f"/api/income/{income_source_id}/events",
        json=_event_payload(event_type="raise", new_base_amount=60_000_000),
    )
    assert r.status_code == 200
    src = client.get(f"/api/income/{income_source_id}").json()
    assert src["base_amount_monthly"] == pytest.approx(60_000_000)


def test_list_events(client, income_source_id):
    client.post(f"/api/income/{income_source_id}/events", json=_event_payload(event_date="2026-01-01"))
    client.post(f"/api/income/{income_source_id}/events", json=_event_payload(event_date="2026-02-01"))
    r = client.get(f"/api/income/{income_source_id}/events")
    assert r.status_code == 200
    assert len(r.json()) == 2


def test_list_events_for_nonexistent_income_source_returns_404(client):
    assert client.get("/api/income/999999/events").status_code == 404


def test_create_event_for_nonexistent_income_source_returns_404(client):
    r = client.post("/api/income/999999/events", json=_event_payload())
    assert r.status_code == 404


def test_create_event_invalid_enum_returns_422(client, income_source_id):
    r = client.post(f"/api/income/{income_source_id}/events", json=_event_payload(event_type="not_a_real_event"))
    assert r.status_code == 422


def test_update_event(client, income_source_id):
    created = client.post(f"/api/income/{income_source_id}/events", json=_event_payload()).json()
    r = client.patch(
        f"/api/income/{income_source_id}/events/{created['id']}",
        json={"notes": "Annual comp review"},
    )
    assert r.status_code == 200
    assert r.json()["notes"] == "Annual comp review"


def test_update_event_for_nonexistent_income_source_returns_404(client, income_source_id):
    created = client.post(f"/api/income/{income_source_id}/events", json=_event_payload()).json()
    r = client.patch(f"/api/income/999999/events/{created['id']}", json={"notes": "x"})
    assert r.status_code == 404


def test_update_nonexistent_event_returns_404(client, income_source_id):
    r = client.patch(f"/api/income/{income_source_id}/events/999999", json={"notes": "x"})
    assert r.status_code == 404


def test_delete_event(client, income_source_id):
    created = client.post(f"/api/income/{income_source_id}/events", json=_event_payload()).json()
    r = client.delete(f"/api/income/{income_source_id}/events/{created['id']}")
    assert r.status_code == 200
    r2 = client.get(f"/api/income/{income_source_id}/events")
    assert r2.json() == []


def test_delete_event_for_nonexistent_income_source_returns_404(client, income_source_id):
    created = client.post(f"/api/income/{income_source_id}/events", json=_event_payload()).json()
    r = client.delete(f"/api/income/999999/events/{created['id']}")
    assert r.status_code == 404


def test_delete_nonexistent_event_returns_404(client, income_source_id):
    r = client.delete(f"/api/income/{income_source_id}/events/999999")
    assert r.status_code == 404


# ── Review-overdue flag ────────────────────────────────────────────────────────


def test_review_overdue_true_when_no_review_event(client, income_source_id):
    r = client.get(f"/api/income/{income_source_id}")
    assert r.json()["review_overdue"] is True


def test_review_overdue_false_after_recent_review_event(client, income_source_id):
    today = date.today().isoformat()
    client.post(f"/api/income/{income_source_id}/events", json=_event_payload(event_date=today, event_type="review"))
    r = client.get(f"/api/income/{income_source_id}")
    assert r.json()["review_overdue"] is False


def test_review_overdue_true_when_review_event_too_old(client, income_source_id):
    stale_date = (date.today() - timedelta(days=400)).isoformat()
    client.post(
        f"/api/income/{income_source_id}/events", json=_event_payload(event_date=stale_date, event_type="review")
    )
    r = client.get(f"/api/income/{income_source_id}")
    assert r.json()["review_overdue"] is True


def test_review_overdue_false_when_income_source_inactive(client, income_source_id):
    client.put(f"/api/income/{income_source_id}", json={"is_active": False})
    r = client.get(f"/api/income/{income_source_id}")
    assert r.json()["review_overdue"] is False
