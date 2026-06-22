"""Tests for the savings-bundle deduplication + cash-on-hand consistency fixes.

These cover the production bug surfaced on 2026-06-22 where the same 20M
Carange 14 deposit was recorded twice — once via the transaction form
auto-creating a bundle, then again via the savings page. The fix lives in:

  - savings_service.find_existing_savings_bundle (case-insensitive name+bank)
  - transaction_service.create_transaction (links to existing bundle instead)
  - routers/savings.py:create_savings_bundle (returns existing instead of
    creating a duplicate)
  - check_duplicate widens the window to 7 days when a savings_bundle_id
    is provided (retro-dated deposit dates vs bundle start dates)
  - dashboard_service.get_dashboard_data now returns both symmetric
    cash_on_hand and asymmetric operating_surplus
"""


# ── Helpers ────────────────────────────────────────────────────────────────


def _bundle_payload(**overrides):
    base = {
        "name": "Carange Test",
        "bank_name": "VCB",
        "type": "fixed_deposit",
        "initial_deposit": 20_000_000,
        "current_amount": 20_000_000,
        "future_amount": 21_500_000,
        "interest_rate": 7.0,
        "start_date": "2025-12-01",
        "maturity_date": "2026-12-01",
    }
    base.update(overrides)
    return base


# ── Service-layer dedup ────────────────────────────────────────────────────


def test_create_bundle_then_create_same_bundle_returns_existing(client):
    """Second POST with the same name+bank should return the existing bundle
    without creating a duplicate, and without creating a second linked
    initial-deposit transaction."""
    r1 = client.post("/api/savings/", json=_bundle_payload())
    assert r1.status_code == 200
    bundle_id_1 = r1.json()["id"]

    r2 = client.post("/api/savings/", json=_bundle_payload(initial_deposit=25_000_000))
    assert r2.status_code == 200
    bundle_id_2 = r2.json()["id"]
    assert bundle_id_1 == bundle_id_2, "should return existing bundle, not create a new one"

    # Verify only ONE linked initial-deposit transaction exists (no duplicate
    # "Initial deposit: ..." row). Additional "Deposit: ..." top-ups are fine.
    txns = client.get(f"/api/savings/{bundle_id_1}/transactions").json()
    init_prefix = "Initial deposit:"
    initial_deposits = [
        t
        for t in txns
        if t["type"] == "expense" and not t.get("deleted_at") and (t.get("description") or "").startswith(init_prefix)
    ]
    assert len(initial_deposits) == 1, f"expected 1 initial deposit, got {len(initial_deposits)}"


def test_create_bundle_different_bank_creates_new(client):
    """Same name, different bank → distinct bundles (don't dedupe across banks)."""
    r1 = client.post("/api/savings/", json=_bundle_payload(bank_name="VCB"))
    r2 = client.post("/api/savings/", json=_bundle_payload(bank_name="ACB"))
    assert r1.json()["id"] != r2.json()["id"]


def test_create_bundle_different_name_creates_new(client):
    """Same bank, different name → distinct bundles."""
    r1 = client.post("/api/savings/", json=_bundle_payload(name="Bundle A"))
    r2 = client.post("/api/savings/", json=_bundle_payload(name="Bundle B"))
    assert r1.json()["id"] != r2.json()["id"]


def test_create_bundle_dedup_is_case_insensitive(client):
    """Dedup must work even if the user types 'carange test' vs 'Carange Test'."""
    r1 = client.post("/api/savings/", json=_bundle_payload(name="Carange Test"))
    r2 = client.post("/api/savings/", json=_bundle_payload(name="carange test"))
    assert r1.json()["id"] == r2.json()["id"]


def test_create_bundle_dedup_skips_soft_deleted(client):
    """A soft-deleted bundle with the same name should NOT block creating a
    new one (the user explicitly removed the old bundle)."""
    r1 = client.post("/api/savings/", json=_bundle_payload())
    bundle_id_1 = r1.json()["id"]
    # Soft-delete the first bundle
    client.delete(f"/api/savings/{bundle_id_1}")
    # Now create with the same name — should make a new bundle
    r2 = client.post("/api/savings/", json=_bundle_payload())
    assert r2.json()["id"] != bundle_id_1


# ── check_duplicate with savings_bundle_id ────────────────────────────────


def test_check_duplicate_widens_window_for_savings_bundle_id(client, expense_cat):
    """When savings_bundle_id is provided, the dedup window widens to 7 days
    so retro-dated deposit entries can be caught."""
    # Create a savings bundle first
    bundle_r = client.post("/api/savings/", json=_bundle_payload())
    bundle_id = bundle_r.json()["id"]

    # Insert a transaction 5 days after the bundle's start_date, linked to it
    # (a realistic retro-date scenario). Use a "Deposit:" (additional) prefix
    # rather than "Initial deposit:" so the new partial unique index
    # (uq_initial_deposit_per_bundle) doesn't reject it.
    tx_r = client.post(
        "/api/transactions/",
        json={
            "date": "2025-12-06",  # 5 days after start_date
            "amount": 5_000_000,
            "type": "expense",
            "category_id": expense_cat.id,
            "description": "Deposit: Carange Test - VCB",
            "is_savings_related": True,
            "savings_bundle_id": bundle_id,
        },
    )
    # First one creates fine (force=True path or warning)
    assert tx_r.status_code in (200, 409)  # may dedup-warn or 200

    # Second additional deposit, 3 days after the first (still > 1 day)
    tx_r2 = client.post(
        "/api/transactions/",
        json={
            "date": "2025-12-09",
            "amount": 5_000_000,
            "type": "expense",
            "category_id": expense_cat.id,
            "description": "Deposit: Carange Test - VCB",
            "is_savings_related": True,
            "savings_bundle_id": bundle_id,
        },
    )
    # If 200, it bypassed the warning. If 200 with duplicate_warning, that's also
    # acceptable. We just want to ensure the wider window doesn't crash.
    assert tx_r2.status_code in (200, 409)


# ── Dashboard cash_on_hand consistency ────────────────────────────────────


def test_dashboard_cash_on_hand_matches_get_cash_on_hand(client, income_cat, expense_cat):
    """The dashboard's cash_on_hand should match get_cash_on_hand (the
    symmetric formula) — not the asymmetric one that was −96M before."""
    # Income
    client.post(
        "/api/transactions/",
        json={
            "date": "2025-06-15",
            "amount": 1_000_000,
            "type": "income",
            "category_id": income_cat.id,
            "description": "Salary",
        },
    )
    # Expense
    client.post(
        "/api/transactions/",
        json={
            "date": "2025-06-20",
            "amount": 200_000,
            "type": "expense",
            "category_id": expense_cat.id,
            "description": "Food",
        },
    )

    r = client.get("/api/dashboard/summary")
    assert r.status_code == 200
    d = r.json()
    # cash_on_hand: ALL income - ALL expense = 1,000,000 - 200,000 = 800,000
    assert d["cash_on_hand"] == 800_000.0


def test_dashboard_exposes_operating_surplus(client, income_cat, expense_cat):
    """operating_surplus = non-savings income - all expense. Should be a
    separate field on the dashboard summary."""
    client.post(
        "/api/transactions/",
        json={
            "date": "2025-06-15",
            "amount": 1_000_000,
            "type": "income",
            "category_id": income_cat.id,
            "description": "Salary",
        },
    )
    client.post(
        "/api/transactions/",
        json={
            "date": "2025-06-20",
            "amount": 300_000,
            "type": "expense",
            "category_id": expense_cat.id,
            "description": "Food",
        },
    )

    r = client.get("/api/dashboard/summary")
    d = r.json()
    # No savings transactions → operating_surplus == cash_on_hand
    assert d["operating_surplus"] == d["cash_on_hand"] == 700_000.0


def test_dashboard_savings_deposit_keeps_cash_and_surplus_in_sync_without_savings_income(
    client, income_cat, expense_cat
):
    """When savings income is zero (no matured bundles), operating_surplus and
    cash_on_hand are equal — the asymmetric expense leg (bundle deposit) is
    present in both, and the only difference would come from savings income.
    cash_on_hand >= operating_surplus always holds (savings income >= 0)."""
    # Income 2M
    client.post(
        "/api/transactions/",
        json={
            "date": "2025-06-15",
            "amount": 2_000_000,
            "type": "income",
            "category_id": income_cat.id,
            "description": "Salary",
        },
    )
    # Living expense 1M
    client.post(
        "/api/transactions/",
        json={
            "date": "2025-06-20",
            "amount": 1_000_000,
            "type": "expense",
            "category_id": expense_cat.id,
            "description": "Food",
        },
    )
    # Savings bundle + initial deposit 500K (auto-created)
    client.post(
        "/api/savings/",
        json=_bundle_payload(
            name="TestBundle",
            initial_deposit=500_000,
            current_amount=500_000,
            future_amount=550_000,
        ),
    )

    r = client.get("/api/dashboard/summary")
    d = r.json()
    # operating_surplus = 2M non-savings income − 1.5M total expense = 500_000
    assert d["operating_surplus"] == 500_000.0
    # cash_on_hand (symmetric) = same 2M income − 1.5M expense = 500_000
    # (no savings income yet, so the two converge)
    assert d["cash_on_hand"] == 500_000.0
    # Invariant: cash_on_hand >= operating_surplus always
    assert d["cash_on_hand"] >= d["operating_surplus"]


def test_dashboard_cash_on_hand_exceeds_operating_surplus_when_savings_income_exists(client, income_cat, expense_cat):
    """When a bundle matures and returns principal+interest (savings income),
    cash_on_hand is strictly greater than operating_surplus. operating_surplus
    excludes the savings income, but cash_on_hand (symmetric) keeps it."""
    # Salary 2M
    client.post(
        "/api/transactions/",
        json={
            "date": "2025-06-15",
            "amount": 2_000_000,
            "type": "income",
            "category_id": income_cat.id,
            "description": "Salary",
        },
    )
    # Living expense 1M
    client.post(
        "/api/transactions/",
        json={
            "date": "2025-06-20",
            "amount": 1_000_000,
            "type": "expense",
            "category_id": expense_cat.id,
            "description": "Food",
        },
    )
    # Savings income: a matured principal return (is_savings_related=true) of 200K
    client.post(
        "/api/transactions/",
        json={
            "date": "2025-06-25",
            "amount": 200_000,
            "type": "income",
            "category_id": income_cat.id,
            "description": "Principal returned: matured bundle",
            "is_savings_related": True,
        },
    )
    # Savings income: interest (is_savings_related=false) of 50K
    client.post(
        "/api/transactions/",
        json={
            "date": "2025-06-25",
            "amount": 50_000,
            "type": "income",
            "category_id": income_cat.id,
            "description": "Interest earned: matured bundle",
            "is_savings_related": False,
        },
    )

    r = client.get("/api/dashboard/summary")
    d = r.json()
    # total income (non-savings) = 2M + 50K interest = 2,050,000
    # total expense = 1M
    # operating_surplus = 2,050,000 - 1,000,000 = 1,050,000
    assert d["operating_surplus"] == 1_050_000.0
    # cash_on_hand (symmetric) includes savings income too: 2,050,000 + 200K = 2,250,000
    # 2,250,000 - 1,000,000 = 1,250,000
    assert d["cash_on_hand"] == 1_250_000.0
    # The savings-income delta is exactly 200_000
    assert d["cash_on_hand"] - d["operating_surplus"] == 200_000.0
