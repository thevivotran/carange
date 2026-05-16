"""Extended project tests — payments CRUD, bulk schedule, filters, stats."""

import pytest


def _project(client, **overrides):
    base = {"name": "Test Project", "type": "real_estate", "priority": "high"}
    base.update(overrides)
    r = client.post("/api/projects/", json=base)
    assert r.status_code == 200
    return r.json()["id"]


@pytest.fixture()
def project_id(client):
    return _project(client)


@pytest.fixture()
def expense_cat(client):
    r = client.post(
        "/api/categories/", json={"name": "Construction", "type": "expense", "color": "#3B82F6", "icon": "home"}
    )
    assert r.status_code == 200
    return r.json()["id"]


# ── Filters ───────────────────────────────────────────────────────────────────


def test_filter_projects_by_status(client):
    _project(client, name="Active")
    r = client.get("/api/projects/?status=planning")
    assert r.status_code == 200
    assert all(p["status"] == "planning" for p in r.json())


def test_filter_projects_by_type(client):
    _project(client, name="RE", type="real_estate")
    _project(client, name="Vehicle", type="vehicle")
    r = client.get("/api/projects/?project_type=real_estate")
    assert r.status_code == 200
    assert all(p["type"] == "real_estate" for p in r.json())


# ── Update project ────────────────────────────────────────────────────────────


def test_update_project_name(client, project_id):
    r = client.put(f"/api/projects/{project_id}", json={"name": "Renamed Project"})
    assert r.status_code == 200
    assert r.json()["name"] == "Renamed Project"


def test_update_project_to_completed_sets_completed_at(client, project_id):
    r = client.put(f"/api/projects/{project_id}", json={"status": "completed"})
    assert r.status_code == 200
    d = r.json()
    assert d["status"] == "completed"
    assert d["completed_at"] is not None


def test_update_nonexistent_project_returns_404(client):
    r = client.put("/api/projects/999999", json={"name": "Ghost"})
    assert r.status_code == 404


def test_delete_nonexistent_project_returns_404(client):
    r = client.delete("/api/projects/999999")
    assert r.status_code == 404


# ── Payments CRUD ─────────────────────────────────────────────────────────────


def test_get_payments_empty(client, project_id):
    r = client.get(f"/api/projects/{project_id}/payments")
    assert r.status_code == 200
    assert r.json() == []


def test_get_payments_nonexistent_project_returns_404(client):
    r = client.get("/api/projects/999999/payments")
    assert r.status_code == 404


def test_create_payment(client, project_id):
    r = client.post(
        f"/api/projects/{project_id}/payments",
        json={"amount": 5_000_000, "due_date": "2026-06-01", "notes": "First payment"},
    )
    assert r.status_code == 200
    d = r.json()
    assert d["amount"] == pytest.approx(5_000_000)
    assert d["status"] == "pending"
    assert d["project_id"] == project_id


def test_create_payment_updates_project_totals(client, project_id):
    client.post(
        f"/api/projects/{project_id}/payments",
        json={"amount": 5_000_000, "due_date": "2026-06-01"},
    )
    client.post(
        f"/api/projects/{project_id}/payments",
        json={"amount": 3_000_000, "due_date": "2026-07-01"},
    )
    project = client.get(f"/api/projects/{project_id}").json()
    assert project["target_amount"] == pytest.approx(8_000_000)


def test_create_payment_nonexistent_project_returns_404(client):
    r = client.post("/api/projects/999999/payments", json={"amount": 1_000})
    assert r.status_code == 404


def test_update_payment_mark_paid(client, project_id, expense_cat):
    payment_id = client.post(
        f"/api/projects/{project_id}/payments",
        json={"amount": 5_000_000, "due_date": "2026-06-01"},
    ).json()["id"]

    r = client.patch(
        f"/api/projects/{project_id}/payments/{payment_id}",
        json={"status": "paid", "category_id": expense_cat, "payment_date": "2026-06-01"},
    )
    assert r.status_code == 200
    d = r.json()
    assert d["status"] == "paid"
    assert d["transaction_id"] is not None


def test_update_payment_mark_paid_updates_project_current(client, project_id, expense_cat):
    payment_id = client.post(
        f"/api/projects/{project_id}/payments",
        json={"amount": 5_000_000, "due_date": "2026-06-01"},
    ).json()["id"]
    client.patch(
        f"/api/projects/{project_id}/payments/{payment_id}",
        json={"status": "paid", "category_id": expense_cat},
    )
    project = client.get(f"/api/projects/{project_id}").json()
    assert project["current_amount"] == pytest.approx(5_000_000)
    assert project["status"] == "in_progress"


def test_update_payment_nonexistent_project_returns_404(client):
    r = client.patch("/api/projects/999999/payments/1", json={"status": "paid"})
    assert r.status_code == 404


def test_update_payment_nonexistent_payment_returns_404(client, project_id):
    r = client.patch(f"/api/projects/{project_id}/payments/999999", json={"status": "paid"})
    assert r.status_code == 404


def test_delete_payment(client, project_id):
    payment_id = client.post(
        f"/api/projects/{project_id}/payments",
        json={"amount": 5_000_000},
    ).json()["id"]
    r = client.delete(f"/api/projects/{project_id}/payments/{payment_id}")
    assert r.status_code == 200
    payments = client.get(f"/api/projects/{project_id}/payments").json()
    assert all(p["id"] != payment_id for p in payments)


def test_delete_payment_updates_project_target(client, project_id):
    payment_id = client.post(
        f"/api/projects/{project_id}/payments",
        json={"amount": 5_000_000},
    ).json()["id"]
    client.delete(f"/api/projects/{project_id}/payments/{payment_id}")
    project = client.get(f"/api/projects/{project_id}").json()
    assert project["target_amount"] == pytest.approx(0)


def test_delete_payment_nonexistent_project_returns_404(client):
    r = client.delete("/api/projects/999999/payments/1")
    assert r.status_code == 404


def test_delete_payment_nonexistent_payment_returns_404(client, project_id):
    r = client.delete(f"/api/projects/{project_id}/payments/999999")
    assert r.status_code == 404


# ── Bulk schedule ─────────────────────────────────────────────────────────────


def test_bulk_create_payments_monthly(client, project_id):
    r = client.post(
        f"/api/projects/{project_id}/payments/bulk",
        json={"amount": 1_000_000, "start_date": "2026-06-01", "interval": "monthly", "occurrences": 3},
    )
    assert r.status_code == 200
    payments = r.json()
    assert len(payments) == 3
    assert payments[0]["due_date"] == "2026-06-01"
    assert payments[1]["due_date"] == "2026-07-01"
    assert payments[2]["due_date"] == "2026-08-01"


def test_bulk_create_payments_weekly(client, project_id):
    r = client.post(
        f"/api/projects/{project_id}/payments/bulk",
        json={"amount": 500_000, "start_date": "2026-06-01", "interval": "weekly", "occurrences": 2},
    )
    assert r.status_code == 200
    payments = r.json()
    assert len(payments) == 2
    assert payments[1]["due_date"] == "2026-06-08"


def test_bulk_create_payments_biweekly(client, project_id):
    r = client.post(
        f"/api/projects/{project_id}/payments/bulk",
        json={"amount": 500_000, "start_date": "2026-06-01", "interval": "biweekly", "occurrences": 2},
    )
    assert r.status_code == 200
    payments = r.json()
    assert payments[1]["due_date"] == "2026-06-15"


def test_bulk_create_payments_nonexistent_project_returns_404(client):
    r = client.post(
        "/api/projects/999999/payments/bulk",
        json={"amount": 1_000_000, "start_date": "2026-06-01", "interval": "monthly", "occurrences": 1},
    )
    assert r.status_code == 404


# ── Link savings ──────────────────────────────────────────────────────────────


def test_link_savings_to_project(client, project_id):
    bundle_id = client.post(
        "/api/savings/",
        json={
            "name": "Bundle",
            "bank_name": "VCB",
            "type": "fixed_deposit",
            "initial_deposit": 10_000_000,
            "future_amount": 10_500_000,
            "start_date": "2026-01-01",
        },
    ).json()["id"]
    r = client.post(f"/api/projects/{project_id}/link-savings/{bundle_id}")
    assert r.status_code == 200
    assert "linked" in r.json()["message"]


def test_link_nonexistent_savings_returns_404(client, project_id):
    r = client.post(f"/api/projects/{project_id}/link-savings/999999")
    assert r.status_code == 404


def test_link_savings_nonexistent_project_returns_404(client):
    bundle_id = client.post(
        "/api/savings/",
        json={
            "name": "Bundle",
            "bank_name": "VCB",
            "type": "fixed_deposit",
            "initial_deposit": 10_000_000,
            "future_amount": 10_500_000,
            "start_date": "2026-01-01",
        },
    ).json()["id"]
    r = client.post(f"/api/projects/999999/link-savings/{bundle_id}")
    assert r.status_code == 404


# ── Stats summary ─────────────────────────────────────────────────────────────


def test_projects_stats_summary_empty(client):
    r = client.get("/api/projects/stats/summary")
    assert r.status_code == 200
    d = r.json()
    assert d["active_projects_count"] == 0
    assert d["completed_projects_count"] == 0
    assert d["total_target_amount"] == pytest.approx(0)


def test_projects_stats_summary_with_data(client):
    pid = _project(client, name="Stats Project")
    client.post(
        f"/api/projects/{pid}/payments",
        json={"amount": 5_000_000},
    )
    r = client.get("/api/projects/stats/summary")
    assert r.status_code == 200
    d = r.json()
    assert d["active_projects_count"] >= 1
    assert d["total_target_amount"] >= 5_000_000
