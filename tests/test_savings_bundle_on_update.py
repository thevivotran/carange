"""Tests for upgrading a transaction to savings-related via PUT.

Covers:
- Normal transaction updated to is_savings_related=True creates a bundle and links it
- Re-editing an already savings-related transaction does NOT create a second bundle (idempotency)
- Sending savings_bundle when the tx already has a bundle is silently ignored
- Updating to is_savings_related=True without savings_bundle data still marks the flag but leaves savings_bundle_id null
- Bundle data is persisted correctly (name, bank_name, interest_rate, dates, future_amount)
- linked_transaction_count on the new bundle is 1
"""

import pytest


@pytest.fixture()
def cat(client):
    return client.post(
        "/api/categories/", json={"name": "Savings", "type": "expense", "color": "#3B82F6", "icon": "piggy-bank"}
    ).json()


def _tx(client, cat_id, *, amount=50_000_000, type_="expense", date="2026-03-01", is_savings_related=False):
    r = client.post(
        "/api/transactions/",
        json={
            "date": date,
            "amount": amount,
            "type": type_,
            "category_id": cat_id,
            "description": "test tx",
            "payment_method": "cash",
            "is_savings_related": is_savings_related,
        },
    )
    assert r.status_code == 200
    return r.json()


def _bundle_payload():
    return {
        "name": "My Bundle",
        "bank_name": "VCB",
        "type": "fixed_deposit",
        "initial_deposit": 50_000_000,
        "future_amount": 53_000_000,
        "interest_rate": 6.0,
        "start_date": "2026-03-01",
        "maturity_date": "2027-03-01",
        "notes": "test note",
    }


# ── Core happy path ───────────────────────────────────────────────────────────


def test_update_to_savings_related_creates_bundle(client, cat):
    tx_id = _tx(client, cat["id"])["id"]

    r = client.put(
        f"/api/transactions/{tx_id}",
        json={"is_savings_related": True, "savings_bundle": _bundle_payload()},
    )
    assert r.status_code == 200
    d = r.json()
    assert d["is_savings_related"] is True
    assert d["savings_bundle_id"] is not None


def test_update_to_savings_related_bundle_persisted_correctly(client, cat):
    tx_id = _tx(client, cat["id"], amount=50_000_000)["id"]
    bundle_payload = _bundle_payload()

    tx = client.put(
        f"/api/transactions/{tx_id}",
        json={"is_savings_related": True, "savings_bundle": bundle_payload},
    ).json()
    bundle_id = tx["savings_bundle_id"]

    r = client.get(f"/api/savings/{bundle_id}")
    assert r.status_code == 200
    b = r.json()
    assert b["name"] == "My Bundle"
    assert b["bank_name"] == "VCB"
    assert b["interest_rate"] == pytest.approx(6.0)
    assert b["initial_deposit"] == pytest.approx(50_000_000)
    assert b["future_amount"] == pytest.approx(53_000_000)
    assert b["maturity_date"] == "2027-03-01"
    assert b["notes"] == "test note"
    assert b["status"] == "active"


def test_update_to_savings_related_bundle_linked_transaction_count(client, cat):
    # linked_transaction_count is only computed by the list endpoint
    tx_id = _tx(client, cat["id"])["id"]

    tx = client.put(
        f"/api/transactions/{tx_id}",
        json={"is_savings_related": True, "savings_bundle": _bundle_payload()},
    ).json()
    bundle_id = tx["savings_bundle_id"]

    bundles = client.get("/api/savings/").json()
    b = next(b for b in bundles if b["id"] == bundle_id)
    assert b["linked_transaction_count"] == 1


# ── Idempotency ───────────────────────────────────────────────────────────────


def test_re_edit_savings_related_tx_does_not_create_second_bundle(client, cat, db_session):
    from app.models.database import SavingsBundle as SBModel

    tx_id = _tx(client, cat["id"])["id"]
    first = client.put(
        f"/api/transactions/{tx_id}",
        json={"is_savings_related": True, "savings_bundle": _bundle_payload()},
    ).json()
    original_bundle_id = first["savings_bundle_id"]

    # Edit again with different bundle data — should NOT create a new bundle
    second = client.put(
        f"/api/transactions/{tx_id}",
        json={
            "is_savings_related": True,
            "savings_bundle": {**_bundle_payload(), "name": "Duplicate Attempt"},
        },
    ).json()
    assert second["savings_bundle_id"] == original_bundle_id

    bundle_count = db_session.query(SBModel).count()
    assert bundle_count == 1


def test_re_edit_savings_related_tx_preserves_existing_bundle_data(client, cat):
    tx_id = _tx(client, cat["id"])["id"]
    first = client.put(
        f"/api/transactions/{tx_id}",
        json={"is_savings_related": True, "savings_bundle": _bundle_payload()},
    ).json()
    bundle_id = first["savings_bundle_id"]

    # Re-edit with a conflicting bundle payload
    client.put(
        f"/api/transactions/{tx_id}",
        json={
            "is_savings_related": True,
            "savings_bundle": {**_bundle_payload(), "name": "Should Be Ignored", "bank_name": "BIDV"},
        },
    )

    b = client.get(f"/api/savings/{bundle_id}").json()
    assert b["name"] == "My Bundle"
    assert b["bank_name"] == "VCB"


# ── No bundle data provided ───────────────────────────────────────────────────


def test_update_to_savings_related_without_bundle_data_sets_flag_only(client, cat):
    """is_savings_related is set but no bundle is created when savings_bundle is absent."""
    tx_id = _tx(client, cat["id"])["id"]

    r = client.put(
        f"/api/transactions/{tx_id}",
        json={"is_savings_related": True},
    )
    assert r.status_code == 200
    d = r.json()
    assert d["is_savings_related"] is True
    assert d["savings_bundle_id"] is None


# ── Unrelated fields not affected ─────────────────────────────────────────────


def test_update_to_savings_related_does_not_change_other_fields(client, cat):
    tx_id = _tx(client, cat["id"], amount=12_345_678, date="2026-02-15")["id"]

    updated = client.put(
        f"/api/transactions/{tx_id}",
        json={"is_savings_related": True, "savings_bundle": _bundle_payload()},
    ).json()

    assert updated["amount"] == pytest.approx(12_345_678)
    assert updated["date"] == "2026-02-15"
    assert updated["category_id"] == cat["id"]


# ── Bundle initial_deposit matches transaction amount ─────────────────────────


# ── GET /api/savings/{id}/transactions ───────────────────────────────────────


def test_get_bundle_transactions_empty(client, cat):
    bundle_id = client.post(
        "/api/savings/",
        json={
            "name": "Empty Bundle",
            "bank_name": "VCB",
            "type": "fixed_deposit",
            "initial_deposit": 50_000_000,
            "future_amount": 53_000_000,
            "start_date": "2026-03-01",
        },
    ).json()["id"]
    r = client.get(f"/api/savings/{bundle_id}/transactions")
    assert r.status_code == 200
    txs = r.json()
    assert len(txs) == 1
    assert txs[0]["amount"] == pytest.approx(50_000_000)
    assert txs[0]["description"] == "Initial deposit: Empty Bundle - VCB"


def test_get_bundle_transactions_after_upgrade(client, cat):
    tx_id = _tx(client, cat["id"])["id"]
    tx = client.put(
        f"/api/transactions/{tx_id}",
        json={"is_savings_related": True, "savings_bundle": _bundle_payload()},
    ).json()
    bundle_id = tx["savings_bundle_id"]

    r = client.get(f"/api/savings/{bundle_id}/transactions")
    assert r.status_code == 200
    txs = r.json()
    assert len(txs) == 1
    assert txs[0]["id"] == tx_id
    assert txs[0]["is_savings_related"] is True


def test_get_bundle_transactions_multiple(client, cat):
    """Two transactions linked to the same bundle both appear."""
    # First tx upgrades and creates the bundle
    tx1_id = _tx(client, cat["id"], amount=30_000_000)["id"]
    tx1 = client.put(
        f"/api/transactions/{tx1_id}",
        json={"is_savings_related": True, "savings_bundle": _bundle_payload()},
    ).json()
    bundle_id = tx1["savings_bundle_id"]

    # Second tx linked directly to the existing bundle
    tx2 = client.post(
        "/api/transactions/",
        json={
            "date": "2026-04-01",
            "amount": 10_000_000,
            "type": "expense",
            "category_id": cat["id"],
            "description": "top-up",
            "payment_method": "cash",
            "is_savings_related": True,
            "savings_bundle_id": bundle_id,
        },
    ).json()

    r = client.get(f"/api/savings/{bundle_id}/transactions")
    assert r.status_code == 200
    ids = {t["id"] for t in r.json()}
    assert tx1_id in ids
    assert tx2["id"] in ids


def test_get_bundle_transactions_nonexistent_bundle_returns_404(client):
    r = client.get("/api/savings/999999/transactions")
    assert r.status_code == 404


def test_get_bundle_transactions_excludes_soft_deleted(client, cat):
    tx_id = _tx(client, cat["id"])["id"]
    tx = client.put(
        f"/api/transactions/{tx_id}",
        json={"is_savings_related": True, "savings_bundle": _bundle_payload()},
    ).json()
    bundle_id = tx["savings_bundle_id"]

    client.delete(f"/api/transactions/{tx_id}")

    r = client.get(f"/api/savings/{bundle_id}/transactions")
    assert r.status_code == 200
    assert r.json() == []


# ── bundle_current_amount_equals_initial_deposit ─────────────────────────────


def test_bundle_current_amount_equals_initial_deposit(client, cat):
    tx_id = _tx(client, cat["id"], amount=30_000_000)["id"]
    payload = {**_bundle_payload(), "initial_deposit": 30_000_000, "future_amount": 31_500_000}

    tx = client.put(
        f"/api/transactions/{tx_id}",
        json={"is_savings_related": True, "savings_bundle": payload},
    ).json()
    b = client.get(f"/api/savings/{tx['savings_bundle_id']}").json()

    assert b["current_amount"] == pytest.approx(30_000_000)
    assert b["initial_deposit"] == pytest.approx(30_000_000)
