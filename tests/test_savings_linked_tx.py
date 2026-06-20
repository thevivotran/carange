"""Tests for auto-created linked transaction when creating/updating savings bundles.

Covers:
- Creating a bundle auto-creates a linked transaction
- Updating bundle initial_deposit syncs the linked tx amount
- Rollover creates linked tx for new bundle
- Deleting linked tx doesn't affect bundle
"""

import pytest


def test_create_bundle_auto_creates_linked_transaction(client):
    """Creating a bundle should auto-create a linked transaction with matching amount."""
    r = client.post(
        "/api/savings/",
        json={
            "name": "Linked Tx Bundle",
            "bank_name": "VCB",
            "type": "fixed_deposit",
            "initial_deposit": 10_000_000,
            "future_amount": 10_500_000,
            "interest_rate": 5.0,
            "start_date": "2026-01-15",
            "maturity_date": "2026-07-15",
        },
    )
    assert r.status_code == 200
    bundle_id = r.json()["id"]

    # Check that the bundle has a linked transaction
    txs = client.get(f"/api/savings/{bundle_id}/transactions").json()
    assert len(txs) == 1
    tx = txs[0]
    assert tx["amount"] == pytest.approx(10_000_000)
    assert tx["is_savings_related"] is True
    assert tx["savings_bundle_id"] == bundle_id
    # Description confirms auto-creation
    assert "Initial deposit" in tx["description"]
    assert "Linked Tx Bundle" in tx["description"]


def test_update_bundle_syncs_linked_tx_amount(client):
    """Updating bundle initial_deposit should sync the linked transaction amount."""
    r = client.post(
        "/api/savings/",
        json={
            "name": "Sync Bundle",
            "bank_name": "VCB",
            "type": "fixed_deposit",
            "initial_deposit": 20_000_000,
            "future_amount": 21_000_000,
            "interest_rate": 5.0,
            "start_date": "2026-02-01",
            "maturity_date": "2026-08-01",
        },
    )
    assert r.status_code == 200
    bundle_id = r.json()["id"]

    # Verify initial linked tx amount
    txs = client.get(f"/api/savings/{bundle_id}/transactions").json()
    assert txs[0]["amount"] == pytest.approx(20_000_000)

    # Update initial_deposit
    r = client.put(
        f"/api/savings/{bundle_id}",
        json={"initial_deposit": 25_000_000},
    )
    assert r.status_code == 200

    # Verify linked tx amount was synced
    txs = client.get(f"/api/savings/{bundle_id}/transactions").json()
    assert len(txs) == 1
    assert txs[0]["amount"] == pytest.approx(25_000_000)


def test_update_bundle_initial_deposit_does_not_duplicate_linked_tx(client):
    """Updating initial_deposit multiple times should not create duplicate linked txs."""
    r = client.post(
        "/api/savings/",
        json={
            "name": "No Duplicate Bundle",
            "bank_name": "VCB",
            "type": "fixed_deposit",
            "initial_deposit": 5_000_000,
            "future_amount": 5_250_000,
            "interest_rate": 5.0,
            "start_date": "2026-03-01",
            "maturity_date": "2026-09-01",
        },
    )
    assert r.status_code == 200
    bundle_id = r.json()["id"]

    # Update deposit twice
    for amount in (7_000_000, 9_000_000):
        r = client.put(f"/api/savings/{bundle_id}", json={"initial_deposit": amount})
        assert r.status_code == 200

    txs = client.get(f"/api/savings/{bundle_id}/transactions").json()
    assert len(txs) == 1
    assert txs[0]["amount"] == pytest.approx(9_000_000)


def test_rollover_creates_linked_tx_for_new_bundle(client):
    """Rolling over a bundle should create a linked transaction for the new bundle."""
    # Create a bundle
    r = client.post(
        "/api/savings/",
        json={
            "name": "Rollover Source",
            "bank_name": "VCB",
            "type": "fixed_deposit",
            "initial_deposit": 30_000_000,
            "future_amount": 31_500_000,
            "interest_rate": 5.0,
            "start_date": "2024-01-01",
            "maturity_date": "2024-07-01",
        },
    )
    assert r.status_code == 200
    bundle_id = r.json()["id"]

    # Rollover the bundle
    r = client.post(f"/api/savings/{bundle_id}/rollover")
    assert r.status_code == 200
    new_bundle = r.json()
    new_bundle_id = new_bundle["id"]

    # New bundle should have a linked transaction with the rolled-over amount
    txs = client.get(f"/api/savings/{new_bundle_id}/transactions").json()
    assert len(txs) == 1
    tx = txs[0]
    assert tx["amount"] == pytest.approx(31_500_000)  # future_amount of source bundle
    assert tx["is_savings_related"] is True
    assert tx["savings_bundle_id"] == new_bundle_id
    assert "Rollover Source (Rollover)" in tx["description"] or "Initial deposit" in tx["description"]


def test_delete_linked_tx_does_not_affect_bundle(client):
    """Deleting the linked transaction should not affect the bundle itself."""
    r = client.post(
        "/api/savings/",
        json={
            "name": "Delete Tx Bundle",
            "bank_name": "VCB",
            "type": "fixed_deposit",
            "initial_deposit": 15_000_000,
            "future_amount": 15_750_000,
            "interest_rate": 5.0,
            "start_date": "2026-04-01",
            "maturity_date": "2026-10-01",
        },
    )
    assert r.status_code == 200
    bundle_id = r.json()["id"]

    # Get linked transaction id
    txs = client.get(f"/api/savings/{bundle_id}/transactions").json()
    assert len(txs) == 1
    tx_id = txs[0]["id"]

    # Delete the linked transaction
    r = client.delete(f"/api/transactions/{tx_id}")
    assert r.status_code == 200

    # Bundle should still exist and be accessible
    r = client.get(f"/api/savings/{bundle_id}")
    assert r.status_code == 200
    assert r.json()["name"] == "Delete Tx Bundle"

    # Transactions list should now be empty (the linked tx is soft-deleted)
    txs = client.get(f"/api/savings/{bundle_id}/transactions").json()
    assert txs == []


def test_add_deposit_to_active_bundle(client):
    """Adding a deposit to an active bundle should create a linked tx and update current_amount."""
    r = client.post(
        "/api/savings/",
        json={
            "name": "Deposit Bundle",
            "bank_name": "VCB",
            "type": "fixed_deposit",
            "initial_deposit": 10_000_000,
            "future_amount": 10_500_000,
            "interest_rate": 5.0,
            "start_date": "2026-01-15",
            "maturity_date": "2026-07-15",
        },
    )
    assert r.status_code == 200
    bundle_id = r.json()["id"]
    initial_current = r.json()["current_amount"]

    # Add a deposit
    r = client.post(
        f"/api/savings/{bundle_id}/deposit",
        json={
            "date": "2026-03-01",
            "amount": 2_000_000,
            "description": "Additional deposit",
        },
    )
    assert r.status_code == 200
    bundle = r.json()
    assert bundle["current_amount"] == pytest.approx(initial_current + 2_000_000)

    # Check linked transaction was created
    txs = client.get(f"/api/savings/{bundle_id}/transactions").json()
    assert len(txs) == 2  # initial deposit + new deposit
    deposit_tx = next(t for t in txs if t["amount"] == 2_000_000)
    assert deposit_tx["is_savings_related"] is True
    assert deposit_tx["savings_bundle_id"] == bundle_id
    assert "Additional deposit" in deposit_tx["description"]


def test_add_deposit_to_inactive_bundle_fails(client):
    """Adding a deposit to a completed bundle should fail."""
    r = client.post(
        "/api/savings/",
        json={
            "name": "Inactive Bundle",
            "bank_name": "VCB",
            "type": "fixed_deposit",
            "initial_deposit": 5_000_000,
            "future_amount": 5_250_000,
            "start_date": "2026-01-01",
            "maturity_date": "2026-02-01",
        },
    )
    assert r.status_code == 200
    bundle_id = r.json()["id"]

    # Mark as completed
    r = client.post(f"/api/savings/{bundle_id}/mark-completed")
    assert r.status_code == 200

    # Try to deposit
    r = client.post(
        f"/api/savings/{bundle_id}/deposit",
        json={"date": "2026-03-01", "amount": 1_000_000},
    )
    assert r.status_code == 400


def test_add_deposit_to_nonexistent_bundle_fails(client):
    """Adding a deposit to a non-existent bundle should return 404."""
    r = client.post(
        "/api/savings/99999/deposit",
        json={"date": "2026-03-01", "amount": 1_000_000},
    )
    assert r.status_code == 404


def test_create_bundle_uses_configured_deposit_category(client):
    """Creating a bundle with a configured deposit category should use that category."""
    # First, create a bundle to auto-create the Tiết kiệm category
    r = client.post(
        "/api/savings/",
        json={
            "name": "Cat Seeder Bundle",
            "bank_name": "VCB",
            "type": "fixed_deposit",
            "initial_deposit": 1_000_000,
            "future_amount": 1_050_000,
            "interest_rate": 5.0,
            "start_date": "2026-01-01",
            "maturity_date": "2026-07-01",
        },
    )
    assert r.status_code == 200

    # Now find the auto-created Tiết kiệm category
    r = client.get("/api/categories/")
    assert r.status_code == 200
    cats = r.json()
    deposit_cat = next((c for c in cats if c["name"] == "Tiết kiệm"), None)
    assert deposit_cat is not None, "Tiết kiệm category should exist"

    # Configure the setting to point at this category
    r = client.post("/settings/savings-deposit-category", data={"savings_deposit_category_id": str(deposit_cat["id"])})
    assert r.status_code == 200

    # Create another bundle — should use the configured category
    r = client.post(
        "/api/savings/",
        json={
            "name": "Configured Cat Bundle",
            "bank_name": "VCB",
            "type": "fixed_deposit",
            "initial_deposit": 10_000_000,
            "future_amount": 10_500_000,
            "interest_rate": 5.0,
            "start_date": "2026-05-01",
            "maturity_date": "2026-11-01",
        },
    )
    assert r.status_code == 200
    bundle_id = r.json()["id"]

    # Linked transaction should use the configured category
    txs = client.get(f"/api/savings/{bundle_id}/transactions").json()
    assert len(txs) == 1
    assert txs[0]["category_id"] == deposit_cat["id"]


def test_transaction_derives_is_savings_related_from_category(client):
    """Creating a tx with a savings category should auto-set is_savings_related=True."""
    # Seed: create a bundle so _get_savings_deposit_category auto-creates Tiết kiệm (has is_savings_category=True)
    r = client.post(
        "/api/savings/",
        json={
            "name": "Seed Bundle",
            "bank_name": "VCB",
            "type": "fixed_deposit",
            "initial_deposit": 1_000_000,
            "future_amount": 1_050_000,
            "interest_rate": 5.0,
            "start_date": "2026-01-01",
            "maturity_date": "2026-07-01",
        },
    )
    assert r.status_code == 200

    # Find the Tiết kiệm category (has is_savings_category=True)
    r = client.get("/api/categories/")
    assert r.status_code == 200
    cats = r.json()
    savings_cat = next((c for c in cats if c["is_savings_category"]), None)
    assert savings_cat is not None, "A savings category should exist"

    # Create an expense transaction with this category (no savings_bundle data)
    r = client.post(
        "/api/transactions/",
        json={
            "date": "2026-06-01",
            "amount": 3_000_000,
            "type": "expense",
            "category_id": savings_cat["id"],
            "description": "Auto-savings test",
            "payment_method": "cash",
        },
    )
    assert r.status_code == 200
    tx = r.json()
    # The backend should have forced is_savings_related=True from the category flag
    assert tx["is_savings_related"] is True, "is_savings_related should be set by category"

    # Now create a tx with a non-savings category — should be False
    regular_cat = next((c for c in cats if c["type"] == "expense" and not c["is_savings_category"]), None)
    if regular_cat:
        r = client.post(
            "/api/transactions/",
            json={
                "date": "2026-06-01",
                "amount": 500_000,
                "type": "expense",
                "category_id": regular_cat["id"],
                "description": "Regular expense test",
                "payment_method": "cash",
            },
        )
        assert r.status_code == 200
        assert r.json()["is_savings_related"] is False


def test_configured_deposit_category_fallback_on_invalid_id(client):
    """Setting an invalid savings_deposit_category_id should fall back gracefully."""
    # Set an invalid category ID (exists but is income type, not expense)
    r = client.post("/settings/savings-deposit-category", data={"savings_deposit_category_id": "invalid"})
    assert r.status_code == 200

    # Create a bundle — should fall back to auto-create Tiết kiệm category
    r = client.post(
        "/api/savings/",
        json={
            "name": "Fallback Bundle",
            "bank_name": "VCB",
            "type": "fixed_deposit",
            "initial_deposit": 5_000_000,
            "future_amount": 5_250_000,
            "interest_rate": 5.0,
            "start_date": "2026-01-01",
            "maturity_date": "2026-07-01",
        },
    )
    assert r.status_code == 200
    bundle_id = r.json()["id"]

    # Should have a linked transaction
    txs = client.get(f"/api/savings/{bundle_id}/transactions").json()
    assert len(txs) == 1
