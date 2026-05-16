"""Extended savings tests — filters, linked project validation, stats, edge cases."""

import pytest


def _bundle(client, **overrides):
    base = {
        "name": "Test Bundle",
        "bank_name": "VCB",
        "type": "fixed_deposit",
        "initial_deposit": 50_000_000,
        "current_amount": 50_000_000,
        "future_amount": 53_000_000,
        "interest_rate": 6.0,
        "start_date": "2026-01-01",
        "maturity_date": "2026-07-01",
    }
    base.update(overrides)
    r = client.post("/api/savings/", json=base)
    assert r.status_code == 200
    return r.json()


# ── Filter by status ──────────────────────────────────────────────────────────


def test_list_savings_filter_by_status(client):
    active_id = _bundle(client, name="Active Bundle")["id"]
    client.post(
        "/api/savings/",
        json={
            "name": "Bundle2",
            "bank_name": "VCB",
            "type": "fixed_deposit",
            "initial_deposit": 10_000_000,
            "future_amount": 10_500_000,
            "start_date": "2026-01-01",
        },
    )
    client.post(f"/api/savings/{active_id}/mark-completed")

    r = client.get("/api/savings/?status=active")
    assert r.status_code == 200
    assert all(b["status"] == "active" for b in r.json())

    r2 = client.get("/api/savings/?status=completed")
    assert r2.status_code == 200
    assert all(b["status"] == "completed" for b in r2.json())


# ── Linked project validation ─────────────────────────────────────────────────


def test_create_bundle_with_linked_project(client):
    project_id = client.post(
        "/api/projects/", json={"name": "House", "type": "real_estate", "priority": "high"}
    ).json()["id"]
    r = client.post(
        "/api/savings/",
        json={
            "name": "Linked Bundle",
            "bank_name": "VCB",
            "type": "fixed_deposit",
            "initial_deposit": 50_000_000,
            "future_amount": 53_000_000,
            "start_date": "2026-01-01",
            "linked_project_id": project_id,
        },
    )
    assert r.status_code == 200
    assert r.json()["linked_project_id"] == project_id


def test_create_bundle_with_nonexistent_project_returns_404(client):
    r = client.post(
        "/api/savings/",
        json={
            "name": "Ghost Bundle",
            "bank_name": "VCB",
            "type": "fixed_deposit",
            "initial_deposit": 50_000_000,
            "future_amount": 53_000_000,
            "start_date": "2026-01-01",
            "linked_project_id": 999999,
        },
    )
    assert r.status_code == 404


def test_update_bundle_with_nonexistent_linked_project_returns_404(client):
    bundle_id = _bundle(client)["id"]
    r = client.put(f"/api/savings/{bundle_id}", json={"linked_project_id": 999999})
    assert r.status_code == 404


# ── current_amount default from initial_deposit ───────────────────────────────


def test_create_bundle_without_current_amount_defaults_to_zero(client):
    """When current_amount is omitted, the schema default of 0 is used."""
    r = client.post(
        "/api/savings/",
        json={
            "name": "No Current",
            "bank_name": "VCB",
            "type": "fixed_deposit",
            "initial_deposit": 50_000_000,
            "future_amount": 53_000_000,
            "start_date": "2026-01-01",
        },
    )
    assert r.status_code == 200
    assert r.json()["current_amount"] == pytest.approx(0)


# ── Update status to completed sets completed_at ──────────────────────────────


def test_update_bundle_status_to_completed_sets_completed_at(client):
    bundle_id = _bundle(client)["id"]
    r = client.put(f"/api/savings/{bundle_id}", json={"status": "completed"})
    assert r.status_code == 200
    d = r.json()
    assert d["status"] == "completed"
    assert d["completed_at"] is not None


# ── Delete nonexistent bundle ─────────────────────────────────────────────────


def test_delete_nonexistent_bundle_returns_404(client):
    r = client.delete("/api/savings/999999")
    assert r.status_code == 404


# ── mark-completed 404 ────────────────────────────────────────────────────────


def test_mark_completed_nonexistent_bundle_returns_404(client):
    r = client.post("/api/savings/999999/mark-completed")
    assert r.status_code == 404


# ── Rollover edge cases ───────────────────────────────────────────────────────


def test_rollover_nonexistent_bundle_returns_404(client):
    r = client.post("/api/savings/999999/rollover")
    assert r.status_code == 404


def test_rollover_completed_bundle_returns_400(client):
    bundle_id = _bundle(client)["id"]
    client.post(f"/api/savings/{bundle_id}/mark-completed")
    r = client.post(f"/api/savings/{bundle_id}/rollover")
    assert r.status_code == 400


# ── Stats summary ─────────────────────────────────────────────────────────────


def test_savings_stats_summary_empty(client):
    r = client.get("/api/savings/stats/summary")
    assert r.status_code == 200
    d = r.json()
    assert d["active_bundles_count"] == 0
    assert d["total_initial_deposit"] == pytest.approx(0)
    assert d["average_interest_rate"] == pytest.approx(0)


def test_update_nonexistent_bundle_returns_404(client):
    r = client.put("/api/savings/999999", json={"future_amount": 1_000})
    assert r.status_code == 404


def test_create_bundle_with_explicit_current_amount(client):
    """Providing current_amount explicitly persists the given value."""
    r = client.post(
        "/api/savings/",
        json={
            "name": "Explicit Current",
            "bank_name": "VCB",
            "type": "fixed_deposit",
            "initial_deposit": 50_000_000,
            "current_amount": 25_000_000,
            "future_amount": 53_000_000,
            "start_date": "2026-01-01",
        },
    )
    assert r.status_code == 200
    assert r.json()["current_amount"] == pytest.approx(25_000_000)


def test_savings_stats_summary_with_data(client):
    _bundle(client, name="B1", initial_deposit=50_000_000, future_amount=53_000_000)
    _bundle(client, name="B2", initial_deposit=30_000_000, future_amount=31_500_000)
    r = client.get("/api/savings/stats/summary")
    assert r.status_code == 200
    d = r.json()
    assert d["active_bundles_count"] == 2
    assert d["total_initial_deposit"] == pytest.approx(80_000_000)
    assert d["total_future_amount"] == pytest.approx(84_500_000)
    assert d["total_interest_earned"] == pytest.approx(4_500_000)
