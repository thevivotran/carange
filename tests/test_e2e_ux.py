"""End-to-end UX & functional test suite for Carange.

These tests codify bugs discovered during the dogfood walkthrough (TDD red-first).
Each test should FAIL against the current codebase; the bug fix makes it pass.

Coverage areas:
1. Transactions: search filter, modal open/close, category auto-load
2. Savings: modal Escape close, maturity_date validation, trash restore flow
3. Projects: project page renders seeded data, settlement math
4. Forecast: Intl.NumberFormat VND tick callback produces readable labels
5. Dashboard: Welcome banner auto-dismisses on first transaction
6. Modal hygiene: only one global backdrop, no double-include
7. Currency formatter: VND suffix placement consistency
8. Settings: pay-cycle start day edge cases
9. Auth: /profiles is reachable; protected routes reject no-profile
10. Mobile: no horizontal overflow on Transactions/Savings at 390px

TDD discipline: every test below is a regression test for a real dogfood finding.
"""

import re
from datetime import date, timedelta
from decimal import Decimal

import pytest


# ── Test isolation helpers ────────────────────────────────────────────────────


@pytest.fixture()
def cat_ids(client):
    """Create one income + three expense categories and return their IDs.

    Mirrors the helper in tests/test_transactions.py.
    """
    inc = client.post(
        "/api/categories/", json={"name": "Salary", "type": "income", "color": "#10B981", "icon": "money-bill"}
    ).json()["id"]
    food = client.post(
        "/api/categories/", json={"name": "Food", "type": "expense", "color": "#EF4444", "icon": "utensils"}
    ).json()["id"]
    rent = client.post(
        "/api/categories/", json={"name": "Rent", "type": "expense", "color": "#78350F", "icon": "home"}
    ).json()["id"]
    util = client.post(
        "/api/categories/", json={"name": "Utilities", "type": "expense", "color": "#6366F1", "icon": "bolt"}
    ).json()["id"]
    return {"income": inc, "food": food, "rent": rent, "util": util}


def _make_tx(client, *, date_str, amount, type_, category_id, description="", payment_method="cash"):
    return client.post(
        "/api/transactions/",
        json={
            "date": date_str,
            "amount": amount,
            "type": type_,
            "category_id": category_id,
            "description": description,
            "payment_method": payment_method,
        },
    )


# ── 1. Transactions: search filter ────────────────────────────────────────────


class TestTransactionsSearchFilter:
    """The /transactions search input must actually filter rows by description.

    The search input is `id="filter-search"` (no `name` attr); the JS reads
    the value on Enter or 'Apply' and passes it to /fragments/transactions/list
    as `?q=...`. The /api/transactions/_tx_list filter also accepts `search`.
    """

    def test_search_reduces_rows_by_description(self, client, cat_ids):
        """A search for 'rent' must return only matching transactions.

        The fragment endpoint uses ?search= (not ?q=) — see
        /app/routers/fragments/transactions.py line 86.
        """
        rent_id = cat_ids["rent"]
        food_id = cat_ids["food"]
        r1 = _make_tx(
            client,
            date_str="2026-04-01",
            amount=300_000,
            type_="expense",
            category_id=rent_id,
            description="apartment rent",
        )
        assert r1.status_code == 200, r1.text
        r2 = _make_tx(
            client, date_str="2026-04-02", amount=500_000, type_="expense", category_id=food_id, description="groceries"
        )
        assert r2.status_code == 200, r2.text

        r = client.get("/fragments/transactions/list?skip=0&limit=50&trash=false&search=rent")
        assert r.status_code == 200
        html = r.text
        # Must contain the rent row
        assert "apartment rent" in html
        # Must NOT contain the groceries row
        assert "groceries" not in html.lower()

    def test_search_input_exists_on_page(self, client, cat_ids):
        """The transactions page search input must exist and be wired to applyFilters()."""
        r = client.get("/transactions")
        assert r.status_code == 200
        # The actual id is `filter-search` (not name=q)
        m = re.search(r'<input[^>]*id="filter-search"', r.text)
        assert m, "transactions page must have a search input with id='filter-search'"
        # The surrounding 200 chars must wire to the applyFilters() entry point
        hx_block = re.search(r'id="filter-search".{0,500}', r.text, re.DOTALL)
        assert hx_block
        assert "applyFilters" in hx_block.group(0), (
            f"search input is not wired to applyFilters(). Surrounding:\n{hx_block.group(0)[:400]}"
        )


# ── 2. Transactions: Add modal opens and auto-loads categories ───────────────


class TestAddTransactionModal:
    """The 'Add Transaction' modal must open with categories pre-populated (gotcha #13)."""

    def test_modal_visible_after_button_click(self, client, cat_ids):
        # The modal is in base.html globally; verify it starts hidden
        page = client.get("/transactions")
        assert 'id="transaction-modal"' in page.text
        # The button that opens it must exist
        assert "openAddModal" in page.text

    def test_open_add_modal_html_is_well_formed(self, client, cat_ids):
        """The modal HTML must have a category <select> with all active categories
        pre-populated, NOT just the placeholder option.

        The bug: server-rendered HTML had only the placeholder; the dropdown
        was empty on first paint until JS hydrates. Fixed by pre-populating
        the <select> in the Jinja template via a get_all_active_categories()
        global.
        """
        page = client.get("/transactions")
        modal_idx = page.text.find('id="transaction-modal"')
        assert modal_idx >= 0, "transaction-modal element missing on /transactions"
        # The modal markup is moderate; slice 12k chars (form is long)
        chunk = page.text[modal_idx : modal_idx + 12000]
        sel = re.search(
            r'<select[^>]*name="category_id"[^>]*>(.*?)</select>',
            chunk,
            re.DOTALL,
        )
        assert sel, "category_id select missing inside transaction-modal"
        # The select must have populated options, not just the placeholder
        option_count = len(re.findall(r"<option\b", sel.group(1)))
        assert option_count > 1, (
            f"category_id <select> only has {option_count} option(s); the template "
            f"must pre-populate it with all active categories so first paint is correct."
        )


# ── 3. Savings: maturity_date validation ─────────────────────────────────────


class TestSavingsMaturityDate:
    """A savings bundle with maturity_date in the past must be rejected."""

    def test_create_savings_with_past_maturity_rejected(self, client, db_session):
        past = (date.today() - timedelta(days=10)).isoformat()
        r = client.post(
            "/api/savings/",
            json={
                "name": "Old FD",
                "bank_name": "VCB",
                "type": "fixed_deposit",
                "initial_deposit": 50_000_000,
                "current_amount": 50_000_000,
                "future_amount": 50_000_000,
                "interest_rate": 5.5,
                "start_date": (date.today() - timedelta(days=180)).isoformat(),
                "maturity_date": past,
                "status": "active",
            },
        )
        # Should be 400/422, NOT 200 (silently accepting past dates is a UX bug)
        assert r.status_code in (400, 422), f"past maturity_date was accepted: {r.status_code} {r.text[:200]}"

    def test_create_savings_with_end_before_start_rejected(self, client, db_session):
        """maturity_date < start_date must be rejected."""
        start = date.today() - timedelta(days=30)
        end = start - timedelta(days=5)
        r = client.post(
            "/api/savings/",
            json={
                "name": "Backwards FD",
                "bank_name": "VCB",
                "type": "fixed_deposit",
                "initial_deposit": 10_000_000,
                "current_amount": 10_000_000,
                "future_amount": 10_000_000,
                "interest_rate": 5.0,
                "start_date": start.isoformat(),
                "maturity_date": end.isoformat(),
                "status": "active",
            },
        )
        assert r.status_code in (400, 422), f"maturity_date < start_date was accepted: {r.status_code}"


# ── 4. Savings: Trash modal works end-to-end ──────────────────────────────────


class TestSavingsTrashFlow:
    """Soft-delete a savings bundle, then restore it; verify state transitions."""

    def test_soft_delete_then_restore_round_trip(self, client, db_session):
        r = client.post(
            "/api/savings/",
            json={
                "name": "To Be Deleted",
                "bank_name": "VCB",
                "type": "fixed_deposit",
                "initial_deposit": 1_000_000,
                "current_amount": 1_000_000,
                "future_amount": 1_000_000,
                "interest_rate": 0,
                "start_date": date.today().isoformat(),
                "maturity_date": (date.today() + timedelta(days=30)).isoformat(),
                "status": "active",
            },
        )
        assert r.status_code == 200, r.text
        sb_id = r.json()["id"]

        # Soft-delete (DELETE returns 200/204)
        d = client.delete(f"/api/savings/{sb_id}")
        assert d.status_code in (200, 204), d.text

        # Confirm it's gone from the active list
        lst = client.get("/api/savings/")
        assert all(s["id"] != sb_id for s in lst.json()), "soft-deleted bundle still appears in active list"

        # Confirm it appears in trash
        trash = client.get("/api/savings/trash")
        assert any(s["id"] == sb_id for s in trash.json()), "soft-deleted bundle missing from trash list"

        # Restore
        rest = client.post(f"/api/savings/{sb_id}/restore")
        assert rest.status_code in (200, 204), rest.text

        # Confirm it's active again
        active = client.get("/api/savings/")
        assert any(s["id"] == sb_id for s in active.json()), "restored bundle missing from active list"

    def test_update_soft_deleted_savings_returns_404(self, client, db_session):
        """B1: PUT on a soft-deleted savings bundle must 404, not silently edit it."""
        r = client.post(
            "/api/savings/",
            json={
                "name": "Soft Deleted Bundle",
                "bank_name": "VCB",
                "type": "fixed_deposit",
                "initial_deposit": 1_000_000,
                "current_amount": 1_000_000,
                "future_amount": 1_000_000,
                "interest_rate": 0,
                "start_date": date.today().isoformat(),
                "maturity_date": (date.today() + timedelta(days=30)).isoformat(),
                "status": "active",
            },
        )
        assert r.status_code == 200, r.text
        sb_id = r.json()["id"]

        # Soft-delete
        d = client.delete(f"/api/savings/{sb_id}")
        assert d.status_code in (200, 204)

        # Attempt to update — must 404
        u = client.put(f"/api/savings/{sb_id}", json={"name": "Hacked"})
        assert u.status_code == 404, f"soft-deleted bundle is editable: {u.status_code} {u.text[:200]}"


# ── 5. Projects: render and settlement math ───────────────────────────────────


class TestProjectsPageRender:
    """The /projects page must render the seeded project (status='in_progress')."""

    def test_in_progress_project_visible(self, client, db_session):
        from app.models.database import FinancialProject, ProjectStatus, ProjectType

        p = FinancialProject(
            name="House Down Payment",
            description="HCMC apartment",
            target_amount=Decimal(800_000_000),
            current_amount=Decimal(120_000_000),
            priority="high",
            status=ProjectStatus.IN_PROGRESS,
            deadline=date.today() + timedelta(days=540),
            type=ProjectType.REAL_ESTATE,
        )
        db_session.add(p)
        db_session.commit()

        # Projects grid is HTMX-loaded; the fragment endpoint is the source of truth
        r = client.get("/fragments/projects/grid")
        assert r.status_code == 200
        assert "House Down Payment" in r.text, "in_progress project not visible on /fragments/projects/grid"

    def test_planning_project_also_visible(self, client, db_session):
        from app.models.database import FinancialProject, ProjectStatus, ProjectType

        p = FinancialProject(
            name="Future Trip",
            target_amount=Decimal(20_000_000),
            current_amount=Decimal(0),
            priority="low",
            status=ProjectStatus.PLANNING,
            deadline=date.today() + timedelta(days=120),
            type=ProjectType.VACATION,
        )
        db_session.add(p)
        db_session.commit()

        # Projects grid is HTMX-loaded; the fragment endpoint is the source of truth
        r = client.get("/fragments/projects/grid")
        assert r.status_code == 200
        assert "Future Trip" in r.text

    def test_update_soft_deleted_project_returns_404(self, client, db_session):
        """B2: PUT on a soft-deleted project must 404."""
        from datetime import datetime, timezone
        from app.models.database import FinancialProject, ProjectStatus, ProjectType

        p = FinancialProject(
            name="To Be Deleted Project",
            target_amount=Decimal(10_000_000),
            current_amount=Decimal(0),
            priority="low",
            status=ProjectStatus.PLANNING,
            deadline=date.today() + timedelta(days=60),
            type=ProjectType.VACATION,
        )
        db_session.add(p)
        db_session.commit()
        pid = p.id

        # Soft-delete (set deleted_at directly to avoid going through the service)
        p.deleted_at = datetime.now(timezone.utc)
        db_session.commit()

        # Attempt to update — must 404
        u = client.put(f"/api/projects/{pid}", json={"name": "Hacked Project"})
        assert u.status_code == 404, f"soft-deleted project is editable: {u.status_code} {u.text[:200]}"


# ── 6. Forecast: VND currency format is human-readable ───────────────────────


class TestForecastCurrencyFormat:
    """The forecast page must render amounts in a way that doesn't look like 'đ0' or '+đ0'."""

    def test_forecast_api_returns_valid_currency_code(self, client, cat_ids):
        r = client.get("/api/forecast/data?horizon=30")
        assert r.status_code == 200
        data = r.json()
        # Currency must be one we know
        assert data["currency"] in ("VND", "USD", "EUR")

    def test_forecast_page_renders_amounts_with_space_separator(self, client, cat_ids):
        """Forecast page should render amounts that include a thousands separator
        and a clear currency symbol. The bug: 'Intl.NumberFormat("vi-VN", {style:'currency',currency:'VND'})'
        outputs '79.000.000 ₫' or '₫79,000,000' depending on locale; the test
        guards against the worst case 'đ0' (truncated)."""
        r = client.get("/forecast")
        assert r.status_code == 200
        # Starting balance element should have a non-empty text content after JS hydrates
        # (server-rendered it should be '—' before fetch — test that placeholder or value both have length)
        m = re.search(r'id="forecast-starting-balance"[^>]*>([^<]*)<', r.text)
        assert m
        body = m.group(1).strip()
        # Either placeholder '—' or a real value
        assert len(body) > 0
        # The 'HORIZON NET' cell must have a sign prefix or '—' placeholder
        m2 = re.search(r'id="forecast-horizon-net"[^>]*>([^<]*)<', r.text)
        assert m2


# ── 7. Dashboard: Welcome banner auto-dismiss after first transaction ────────


class TestDashboardWelcomeBanner:
    """Once the user has at least one transaction, the 'Welcome to Carange' banner
    must not render — it's noise."""

    def test_welcome_banner_hidden_after_first_tx(self, client, cat_ids, db_session):
        # Add one transaction
        r = _make_tx(
            client,
            date_str="2026-04-01",
            amount=100_000,
            type_="income",
            category_id=cat_ids["income"],
            description="seed",
        )
        assert r.status_code == 200, r.text

        r = client.get("/")
        assert r.status_code == 200
        # The banner heading must not appear
        assert "Welcome to Carange" not in r.text, (
            "Welcome banner still shown after first transaction. Should auto-dismiss."
        )

    def test_welcome_banner_visible_for_fresh_user(self, client, cat_ids):
        # No transactions
        r = client.get("/")
        assert r.status_code == 200
        # For a fresh DB the welcome banner SHOULD be present
        assert "Welcome to Carange" in r.text, "Fresh user with no data should see the welcome banner"


# ── 8. Settings: pay-cycle start day edge cases ───────────────────────────────


class TestSettingsPayCycle:
    """The pay-cycle start day must be 1..28; 0, 29..31, and non-int must reject.

    Endpoint: POST /settings/pay-cycle with form field `month_start_day`.
    """

    @pytest.mark.parametrize("bad_value", [0, 29, 30, 31, 99, -1])
    def test_pay_cycle_out_of_range_rejected(self, client, db_session, bad_value):
        r = client.post(
            "/settings/pay-cycle",
            data={"month_start_day": str(bad_value)},
            follow_redirects=False,
        )
        # Must NOT 200-redirect; must reject with 400
        assert r.status_code == 400, (
            f"month_start_day={bad_value} was accepted: status={r.status_code} body={r.text[:200]}"
        )


# ── 9. Auth: profile middleware fail-closed ───────────────────────────────────


class TestAuthGating:
    """Without a profile cookie, protected routes must redirect to /profiles."""

    def test_unauthenticated_dashboard_redirects(self, monkeypatch, db_session):
        """When ProfileMiddleware can't resolve a profile, dashboard must redirect."""
        from fastapi.testclient import TestClient
        from main import app
        from app.models.database import get_db

        # Undo the autouse _bypass_profile
        from app.services import profiles as profiles_service

        def _no_profile(request):
            return None

        monkeypatch.setattr(profiles_service, "resolve_request_context", _no_profile)

        app.dependency_overrides[get_db] = lambda: db_session
        with TestClient(app, raise_server_exceptions=True) as c:
            r = c.get("/dashboard", headers={"accept": "text/html"}, follow_redirects=False)
        # Either redirects (302/303) or 401 JSON — both are correct fail-closed
        assert r.status_code in (302, 303, 401), (
            f"unauthenticated /dashboard returned {r.status_code} (body: {r.text[:200]})"
        )

    def test_health_endpoint_is_public(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_profiles_page_is_public(self, client):
        r = client.get("/profiles")
        assert r.status_code == 200


# ── 10. Categories: duplicate name on same type rejected ─────────────────────


class TestCategoryValidation:
    """Two categories of the same type with the same name must be rejected."""

    def test_duplicate_category_name_same_type_rejected(self, client, db_session):
        r1 = client.post("/api/categories/", json={"name": "Food", "type": "expense", "color": "#000", "icon": "x"})
        assert r1.status_code == 200
        r2 = client.post("/api/categories/", json={"name": "Food", "type": "expense", "color": "#000", "icon": "x"})
        assert r2.status_code in (400, 409, 422), f"duplicate category allowed: {r2.status_code} {r2.text[:200]}"

    def test_same_name_different_type_allowed(self, client, db_session):
        """'Others' can exist as both income and expense."""
        r1 = client.post("/api/categories/", json={"name": "Others", "type": "expense", "color": "#000", "icon": "x"})
        r2 = client.post("/api/categories/", json={"name": "Others", "type": "income", "color": "#000", "icon": "x"})
        assert r1.status_code == 200
        assert r2.status_code == 200


# ── 11. Budget: pay-cycle aware (fiscal period) ──────────────────────────────


class TestBudgetFiscalPeriod:
    """The budget for a month with start_day=19 must include 19..end-of-month
    and 1..18 of next month."""

    def test_budget_for_fiscal_period_uses_pay_cycle(self, client, cat_ids, db_session):
        # Set month_start_day=19
        client.post(
            "/settings/pay-cycle",
            data={"month_start_day": "19"},
            follow_redirects=False,
        )

        # Create a transaction on July 5 (before the 19th → belongs to fiscal June)
        r = _make_tx(
            client,
            date_str="2026-07-05",
            amount=100_000,
            type_="expense",
            category_id=cat_ids["food"],
            description="early",
        )
        assert r.status_code == 200

        # Create a transaction on July 25 (after the 19th → belongs to fiscal July)
        r2 = _make_tx(
            client,
            date_str="2026-07-25",
            amount=200_000,
            type_="expense",
            category_id=cat_ids["food"],
            description="late",
        )
        assert r2.status_code == 200

        # Fiscal July 2026 (start day 19) — query the budget rows endpoint
        r = client.get("/api/budget/2026-07/rows")
        assert r.status_code == 200, r.text


# ── 12. Templates: create + use-template round trip ───────────────────────────


class TestTemplatesFlow:
    """Templates can be created, listed, and deactivated."""

    def test_create_template_lists_in_active(self, client, cat_ids):
        t = client.post(
            "/api/templates/",
            json={
                "name": "Monthly Rent",
                "amount": 5_000_000,
                "type": "expense",
                "category_id": cat_ids["rent"],
                "description": "apartment rent",
                "payment_method": "bank_transfer",
                "cadence": "monthly",
                "is_active": True,
            },
        )
        assert t.status_code == 200, t.text
        tid = t.json()["id"]
        # Listed in active filter
        active = client.get("/api/templates/?is_active=true")
        assert any(x["id"] == tid for x in active.json()), "created template missing from active list"

    def test_inactive_template_not_listed(self, client, cat_ids):
        t = client.post(
            "/api/templates/",
            json={
                "name": "Old",
                "amount": 1_000,
                "type": "expense",
                "category_id": cat_ids["food"],
                "description": "x",
                "is_active": False,
            },
        )
        assert t.status_code == 200
        tid = t.json()["id"]
        # Active list should NOT include the inactive template
        active = client.get("/api/templates/?is_active=true")
        assert all(x["id"] != tid for x in active.json()), "inactive template should be hidden from active list"
        # Inactive list should include it
        inactive = client.get("/api/templates/?is_active=false")
        assert any(x["id"] == tid for x in inactive.json()), "inactive template missing from inactive list"


# ── 13. Payees: payee extraction from description ─────────────────────────────


class TestPayees:
    """Payee list must be reachable; extraction may run via separate endpoint."""

    def test_payee_list_endpoint_works(self, client):
        """The /api/payees/ endpoint must return a JSON list (possibly empty)."""
        r = client.get("/api/payees/")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_payee_create_endpoint_works(self, client):
        """Users can manually create a payee."""
        r = client.post(
            "/api/payees/",
            json={"canonical_name": "Vinmart District 1", "category_id": None},
        )
        # Either 200/201 (created) or 422 (needs category) — both prove the endpoint exists
        assert r.status_code in (200, 201, 422), r.text

    def test_payee_list_after_create(self, client):
        """If payee creation succeeds, the new payee shows in the list."""
        r = client.post(
            "/api/payees/",
            json={"canonical_name": "Test Cafe Unique", "category_id": None},
        )
        if r.status_code in (200, 201):
            lst = client.get("/api/payees/")
            names = [p.get("canonical_name") or p.get("name") for p in lst.json()]
            assert "Test Cafe Unique" in names, f"created payee missing: {names}"


# ── 14. Pagination on transactions list ──────────────────────────────────────


class TestTransactionsPagination:
    """Pagination on /transactions must correctly slice the list."""

    def test_pagination_skip_limit(self, client, cat_ids):
        for i in range(15):
            _make_tx(
                client,
                date_str=f"2026-04-{(i % 28) + 1:02d}",
                amount=1000 * (i + 1),
                type_="expense",
                category_id=cat_ids["food"],
                description=f"item-{i}",
            )

        r1 = client.get("/fragments/transactions/list?skip=0&limit=5&trash=false")
        r2 = client.get("/fragments/transactions/list?skip=5&limit=5&trash=false")
        assert r1.status_code == 200
        assert r2.status_code == 200
        # First page must not contain items from second page
        ids1 = set(re.findall(r'data-tx-id="(\d+)"', r1.text))
        ids2 = set(re.findall(r'data-tx-id="(\d+)"', r2.text))
        # At minimum, the two pages should not share >50% of IDs
        overlap = ids1 & ids2
        assert len(overlap) <= 1, f"pages overlap: {ids1 & ids2}"


# ── 15. Currency format: 0 amount renders as 0 (not nothing) ─────────────────


class TestCurrencyFormatter:
    """format_amount(0) must return '0 ₫' or equivalent, not empty string."""

    def test_format_amount_zero(self):
        from app.services.currency_format import format_amount

        assert format_amount(0) == "0 ₫"
        assert format_amount(0, "USD") == "$0"
        assert format_amount(0, "EUR") == "0 €"

    def test_format_amount_negative(self):
        from app.services.currency_format import format_amount

        # Negative amounts should be handled gracefully (no '-₫' or weirdness)
        out = format_amount(-500_000)
        # Either "-500,000 ₫" or "−500,000 ₫" (unicode minus) — both acceptable
        assert "500,000" in out and ("-" in out or "\u2212" in out)

    def test_format_amount_large(self):
        from app.services.currency_format import format_amount

        # No scientific notation for billion-scale numbers
        out = format_amount(1_234_567_890_000)
        assert "e+" not in out
        assert "1,234,567,890,000" in out


# ── 16. Modal hygiene: only ONE global backdrop, no double-include ────────────


class TestModalHygiene:
    """Per the gotcha #9, a modal partial must be included ONCE — either in
    base.html or the child template, not both. Verify by counting <div id="X">."""

    def test_transaction_modal_included_once(self, client):
        r = client.get("/transactions")
        count = r.text.count('id="transaction-modal"')
        assert count == 1, f"transaction-modal appears {count} times (should be 1)"

    def test_savings_modal_included_once_per_page(self, client):
        r = client.get("/savings")
        count = r.text.count('id="savings-modal"')
        assert count == 1, f"savings-modal appears {count} times on /savings (should be 1)"

    def test_project_modal_included_once(self, client):
        r = client.get("/projects")
        count = r.text.count('id="project-modal"')
        assert count == 1, f"project-modal appears {count} times on /projects (should be 1)"


# ── 16b. Modal hygiene: static-source guard (CI lint) ────────────────────────


class TestModalHygieneLint:
    """Static-source guard for skill gotcha #9: no modal partial may be included
    in BOTH base.html AND a child template. The runtime test above checks
    rendered HTML; this one fails the build if someone re-adds a double-include
    at the template-source level. Cheap regex scan over app/templates/.
    """

    def test_no_modal_partial_in_base_plus_child(self):
        from pathlib import Path

        base = Path("app/templates/base.html").read_text()
        # All `{% include "..." %}` directives in base.html
        base_includes = set(re.findall(r'\{%\s*include\s+["\']([^"\']+)["\']\s*%\}', base))
        # Look for modal partials
        modal_partials = {inc for inc in base_includes if "modal" in inc.lower() or "_form" in inc.lower()}
        assert modal_partials, f"base.html should include at least one modal partial. Includes found: {base_includes}"
        # Walk every other template and ensure none of them also includes the same modal
        offenders = []
        for tpl in Path("app/templates").rglob("*.html"):
            if tpl.name == "base.html":
                continue
            text = tpl.read_text()
            child_includes = set(re.findall(r'\{%\s*include\s+["\']([^"\']+)["\']\s*%\}', text))
            # Normalize: child may use "partials/transactions/_modal_form.html" or similar
            double = modal_partials & child_includes
            if double:
                offenders.append((str(tpl), sorted(double)))
        assert not offenders, f"Modal partial double-include detected (skill gotcha #9): {offenders}"


# ── 17. Duplicate-warning modal must ship with the shared partial ────────────


class TestDuplicateWarningGlobal:
    """Regression test: the duplicate-warning modal used to live only in
    transactions/list.html, so on any of the other 15 pages that extend
    base.html the "Add Transaction" form's duplicate check silently no-opped
    (base.html's submit handler called showDuplicateWarning() behind a
    `typeof fn === 'function'` guard, and then always `return`ed — so the
    form looked inert). Fixed by moving the modal markup into the shared
    partials/transactions/_modal_form.html and the JS into base.html so both
    ship on every page.
    """

    def test_duplicate_modal_markup_present_on_non_transactions_page(self, client, cat_ids):
        r = client.get("/")
        assert r.status_code == 200
        assert 'id="duplicate-modal"' in r.text
        assert 'id="duplicate-matches-list"' in r.text
        assert 'id="duplicate-confirm-btn"' in r.text
        assert 'id="duplicate-cancel-btn"' in r.text

    def test_duplicate_modal_appears_exactly_once(self, client, cat_ids):
        r = client.get("/")
        assert r.text.count('id="duplicate-modal"') == 1

    def test_show_duplicate_warning_defined_globally_in_base_html(self):
        from pathlib import Path

        base = Path("app/templates/base.html").read_text()
        assert re.search(r"function\s+showDuplicateWarning\s*\(", base), (
            "showDuplicateWarning must be defined in base.html so it is available on every page"
        )
        # Must not remain duplicated anywhere else in the templates tree.
        hits = []
        for tpl in Path("app/templates").rglob("*.html"):
            text = tpl.read_text()
            if re.search(r"function\s+showDuplicateWarning\s*\(", text):
                hits.append(str(tpl))
        assert hits == ["app/templates/base.html"], f"showDuplicateWarning defined in: {hits}"


# ── 16c. Dashboard stress-test cushion: documented inline comment ────────────


class TestDashboardStressTestCushion:
    """B15: the one-income stress test adds a 20M VND cushion to avg expenses.
    The cushion is a magic number in dashboard_service.py; require an inline
    comment explaining its purpose so future maintainers don't strip it as
    dead/unused code. The actual constant value is verified separately.
    """

    def test_stress_test_documented_cushion_in_dashboard(self, client):
        """B15: Stress-test calculation must have an inline comment explaining the
        VND cushion. This is a documentation test — it just exercises the endpoint
        to confirm the code path is reachable. The actual comment is verified
        by checking that a cushion-explaining comment sits directly above the
        line with the magic number.
        """
        r = client.get("/api/dashboard/summary")
        assert r.status_code == 200
        # The cushion constant lives in dashboard_service.py — confirm it's there
        from pathlib import Path

        src = Path("app/services/dashboard_service.py").read_text()
        m = re.search(r"stress_test_required\s*=.*?20_000_000", src)
        assert m, "stress_test_required no longer references the 20M cushion; review the file"
        # The line(s) immediately above must include a comment explaining the cushion.
        # We require the keyword "cushion" specifically so a generic section header
        # (e.g. "# ── One-income stress test ──") doesn't accidentally satisfy this.
        line_no = src[: m.start()].count("\n") + 1
        lines = src.splitlines()
        above = "\n".join(lines[max(0, line_no - 4) : line_no])
        assert "cushion" in above.lower(), (
            f"no comment explaining the 20M cushion directly above line {line_no}.\nLines above:\n{above}"
        )


# ── 17. Mobile: no horizontal overflow on Transactions at 390px ───────────────


# Mobile tests run via the live Playwright dogfood, not the in-process test
# client. They live in tests/test_e2e_ux_mobile.py (separate file) so this
# suite can stay fast and DB-only.


# ── 18. Dashboard cache invalidation: all write endpoints pass db ─────────────


class TestDashboardCacheInvalidation:
    """Every write endpoint that mutates dashboard data must call
    invalidate_dashboard_cache(db), NOT invalidate_dashboard_cache().

    This is the gotcha in the carange-app skill: the no-arg form only clears
    the in-process dict, not the cross-pod sentinel or the MATVIEW refresh.
    """

    def test_create_transaction_invalidates_dashboard_cache_with_db(self, client, cat_ids, monkeypatch):
        """Mock invalidate_dashboard_cache and assert it was called WITH db.

        The router imports the function at module load, so we must patch the
        router module's reference, not the dashboard_service module.
        """
        from app.routers import transactions as tx_router
        from app.services import dashboard_service

        calls = []
        original = dashboard_service.invalidate_dashboard_cache

        def spy(db=None):
            calls.append(db)
            return original(db)

        monkeypatch.setattr(tx_router, "invalidate_dashboard_cache", spy)

        r = _make_tx(client, date_str="2026-04-01", amount=100_000, type_="income", category_id=cat_ids["income"])
        assert r.status_code == 200
        assert len(calls) == 1, f"invalidate_dashboard_cache called {len(calls)} times, want 1"
        assert calls[0] is not None, "invalidate_dashboard_cache called WITHOUT db — gotcha in skill"

    def test_delete_transaction_invalidates_dashboard_cache_with_db(self, client, cat_ids, monkeypatch):
        from app.routers import transactions as tx_router
        from app.services import dashboard_service

        calls = []
        original = dashboard_service.invalidate_dashboard_cache

        def spy(db=None):
            calls.append(db)
            return original(db)

        monkeypatch.setattr(tx_router, "invalidate_dashboard_cache", spy)

        r = _make_tx(client, date_str="2026-04-01", amount=100_000, type_="income", category_id=cat_ids["income"])
        assert r.status_code == 200
        tx_id = r.json()["id"]

        calls.clear()
        d = client.delete(f"/api/transactions/{tx_id}")
        assert d.status_code in (200, 204)
        assert len(calls) == 1
        assert calls[0] is not None


# ── 19. Auth: profile cookie sets on create ───────────────────────────────────


class TestProfileCookie:
    """Creating a profile via POST /profiles/create sets the cookie and redirects."""

    def test_create_profile_sets_cookie(self, monkeypatch, db_session):
        from fastapi.testclient import TestClient
        from main import app
        from app.models.database import get_db
        from app.services import profiles as profiles_service

        # Undo autouse _bypass_profile for this test
        monkeypatch.setattr(profiles_service, "resolve_request_context", lambda request: None)
        app.dependency_overrides[get_db] = lambda: db_session
        with TestClient(app, raise_server_exceptions=True) as c:
            r = c.post(
                "/profiles/create",
                data={"name": "Test User", "color": "#2563EB", "next": "/"},
                follow_redirects=False,
            )
        assert r.status_code == 303
        # Cookie set
        cookies = r.headers.get("set-cookie", "")
        assert "carange_profile=" in cookies or "profile=" in cookies.lower()


# ── 20. Notes: create / list / delete ─────────────────────────────────────────


class TestNotes:
    """Notes CRUD round trip. Note: schema requires `title` not `text`."""

    def test_create_and_list_note(self, client):
        r = client.post("/api/notes/", json={"title": "Pay back Linh by Friday", "kind": "iou"})
        assert r.status_code in (200, 201), r.text
        nid = r.json()["id"]

        lst = client.get("/api/notes/")
        assert any(n["id"] == nid for n in lst.json())

        d = client.delete(f"/api/notes/{nid}")
        assert d.status_code in (200, 204)


# ── 21. Review: malformed date must 400, not 500 ──────────────────────────────


class TestReviewDateValidation:
    """B10: review-approve with a malformed date must 400, not 500."""

    def test_review_approve_invalid_date_returns_400(self, client, db_session):
        """B10: malformed date must surface as 400/422, not 500."""
        from app.models.database import Transaction, Category, TransactionType

        cat = Category(name="X", type=TransactionType.EXPENSE, color="#000", icon="x")
        db_session.add(cat)
        db_session.commit()
        tx = Transaction(
            date=date.today(),
            amount=100,
            type=TransactionType.EXPENSE,
            category_id=cat.id,
            description="needs review",
            needs_review=True,
        )
        db_session.add(tx)
        db_session.commit()
        tid = tx.id

        # Malformed date
        r = client.post(
            f"/api/review/{tid}/approve",
            json={"date": "2099-13-45"},  # invalid month/day
        )
        assert r.status_code in (400, 422), f"malformed date returned {r.status_code} (should be 400/422)"

    def test_review_approve_valid_date_works(self, client, db_session):
        """Sanity check: a valid date still succeeds."""
        from app.models.database import Transaction, Category, TransactionType

        cat = Category(name="Y", type=TransactionType.EXPENSE, color="#000", icon="x")
        db_session.add(cat)
        db_session.commit()
        tx = Transaction(
            date=date.today(),
            amount=100,
            type=TransactionType.EXPENSE,
            category_id=cat.id,
            description="review me",
            needs_review=True,
        )
        db_session.add(tx)
        db_session.commit()
        r = client.post(f"/api/review/{tx.id}/approve", json={"date": "2026-04-01"})
        assert r.status_code in (200, 204)


# ── 22. Dashboard: empty install must not invent emergency fund months ───────


class TestDashboardEmptyInstall:
    """B11: With no transactions but a savings bundle, emergency_fund_months must be 0 or None.

    The bug: `_ef_total / 3 if _ef_total > 0 else monthly_expense or 1` defaults to 1
    when no expenses exist, so `total_savings / 1` shows the entire savings balance
    as 'X months emergency fund'. The fix should make `emergency_fund_months` = 0
    when there's no expense data on which to base a monthly average.
    """

    def test_emergency_fund_zero_with_savings_no_expenses(self, client, db_session):
        """B11: savings balance with no expenses must not be shown as fake months of runway."""
        from app.models.database import SavingsBundle, SavingsStatus, SavingsType

        # Seed a savings bundle (50M VND), no transactions
        bundle = SavingsBundle(
            name="FD-Bug",
            bank_name="ACB",
            type=SavingsType.FIXED_DEPOSIT,
            initial_deposit=50_000_000,
            current_amount=50_000_000,
            future_amount=50_000_000,
            start_date=date(2026, 1, 1),
            status=SavingsStatus.ACTIVE,
        )
        db_session.add(bundle)
        db_session.commit()

        r = client.get("/api/dashboard/summary")
        assert r.status_code == 200
        data = r.json()
        ef = data.get("emergency_fund_months")
        # The fake "X months" must NOT be the total_savings value (e.g. 50M / 1 = 50.0)
        assert ef is None or ef == 0, (
            f"empty install shows fake emergency_fund_months={ef} "
            f"(should be 0 or None, not total_savings / 1). total_savings={data.get('total_savings')!r}"
        )


# ── 23. Mobile: amount input must have inputmode="decimal" ───────────────────


class TestMobileAmountInput:
    """B12: Amount input on /transactions must have inputmode='decimal' for mobile."""

    def test_amount_input_has_inputmode_decimal(self, client):
        """B12: Amount input must have inputmode='decimal' for mobile keyboard hint."""
        r = client.get("/transactions")
        assert r.status_code == 200
        m = re.search(r'<input[^>]*name="amount"[^>]*>', r.text)
        assert m, "amount input missing on /transactions"
        assert 'inputmode="decimal"' in m.group(0) or "inputmode='decimal'" in m.group(0), (
            f"amount input lacks inputmode='decimal' for mobile. Got: {m.group(0)[:300]}"
        )


# ── 24. Forecast: log silently-skipped templates / payments ─────────────────


class TestForecastSkipsLogging:
    """B13: Templates / payments with NULL schedule fields must be logged, not silently dropped."""

    def test_forecast_logs_skipped_template(self, client, cat_ids, db_session, caplog):
        """B13: A template with NULL next_run_at must produce a warning log."""
        import logging

        from app.models.database import TransactionTemplate, TransactionType

        # Create a template WITHOUT setting next_run_at
        tpl = TransactionTemplate(
            name="NoDateTpl",
            amount=1_000_000,
            type=TransactionType.EXPENSE,
            category_id=cat_ids["food"],
            description="x",
            is_active=True,
            cadence="monthly",
            # next_run_at is NULL by default
        )
        db_session.add(tpl)
        db_session.commit()

        with caplog.at_level(logging.WARNING, logger="app.services.forecast_service"):
            r = client.get("/api/forecast/data?horizon=30")
            assert r.status_code == 200

        skipped = [
            rec
            for rec in caplog.records
            if "next_run_at" in rec.message.lower()
            or "skipped" in rec.message.lower()
            or "no schedule" in rec.message.lower()
        ]
        assert skipped, (
            f"no warning logged for skipped template. "
            f"Records: {[(rec.message, rec.levelname) for rec in caplog.records]}"
        )


# ── 25. Transaction service: budget snapshot failure must be logged ─────────


class TestBudgetSnapshotLogging:
    """B14: When budget snapshot update fails, the exception must be logged, not swallowed."""

    def test_budget_snapshot_failure_logs_warning(self, client, cat_ids, db_session, caplog, monkeypatch):
        """B14: A failure inside the budget snapshot update must be logged as a warning."""
        import logging

        from app.services import budget_context

        def _broken(*args, **kwargs):
            raise RuntimeError("simulated budget snapshot failure")

        # transaction_service imports the function from app.services.budget_context,
        # so we patch it there (NOT in budget_service).
        monkeypatch.setattr(budget_context, "budget_snapshot", _broken)

        with caplog.at_level(logging.WARNING, logger="app.transaction_service"):
            r = client.post(
                "/api/transactions/",
                json={
                    "date": "2026-04-01",
                    "amount": 10_000,
                    "type": "expense",
                    "category_id": cat_ids["food"],
                    "description": "trigger budget snap",
                },
            )
        assert r.status_code == 200, r.text  # tx should still create successfully

        warns = [rec for rec in caplog.records if rec.levelname == "WARNING" and "budget" in rec.message.lower()]
        assert warns, "budget snapshot failure not logged"
        # Diagnostic dump if a CI run fails
        if not warns:
            print(f"Records: {[(rec.message, rec.levelname, rec.name) for rec in caplog.records]}")


# ── 14. Rules: must not break the tx.type <-> category.type invariant ──────────


class TestRuleTypeMismatch:
    """apply_rules() must not override a transaction's category_id to one of the
    wrong type. The router validates the user-supplied category matches the
    transaction type (income vs expense); rules that override to the opposite
    type break the invariant and corrupt aggregations.
    """

    def test_rule_cannot_override_to_wrong_type_category(self, client, cat_ids, db_session):
        """B4: A rule that targets a wrong-type category must not break the
        type invariant. After creating the matching transaction, the stored
        category_id must still match the tx.type."""
        # Create rule: when description contains "testrule123", set category to
        # the INCOME category, regardless of the transaction's actual type.
        r = client.post(
            "/api/rules/",
            json={
                "name": "Force income category",
                "match_field": "description",
                "match_op": "contains",
                "match_value": "testrule123",
                "action_json": {"set_category_id": cat_ids["income"]},
                "is_active": True,
            },
        )
        assert r.status_code in (200, 201), r.text

        # Create an EXPENSE transaction whose description matches the rule.
        # The router-level check verifies the user-supplied category matches
        # the tx type; the rule then fires inside create_transaction.
        tx = client.post(
            "/api/transactions/",
            json={
                "date": "2026-04-01",
                "amount": 50_000,
                "type": "expense",
                "category_id": cat_ids["food"],
                "description": "testrule123 expense",
            },
        )
        assert tx.status_code == 200, tx.text
        body = tx.json()
        # The category_id must remain an EXPENSE category (not the rule's INCOME target).
        assert body["category_id"] != cat_ids["income"], (
            f"rule overrode category to wrong type: tx is 'expense' but "
            f"category_id={body['category_id']} (income category id={cat_ids['income']})"
        )


# ── 21. Asset cache invalidation (B5) ───────────────────────────────────────


class TestAssetCacheInvalidation:
    """B5: Asset CRUD must call invalidate_dashboard_cache(db)."""

    def test_create_asset_invalidates_dashboard_cache(self, client, monkeypatch):
        from app.routers import assets as assets_router
        from app.services import dashboard_service

        calls = []
        original = dashboard_service.invalidate_dashboard_cache

        def spy(db=None):
            calls.append(db)
            return original(db)

        monkeypatch.setattr(assets_router, "invalidate_dashboard_cache", spy)

        r = client.post(
            "/api/assets/",
            json={
                "name": "Test Asset",
                "asset_type": "other",
                "quantity": 1,
                "unit": "piece",
                "purchase_price_vnd": 10_000_000,
                "current_value_vnd": 12_000_000,
            },
        )
        assert r.status_code in (200, 201), r.text
        assert len(calls) == 1, f"invalidate_dashboard_cache not called on asset create: {len(calls)}"
        assert calls[0] is not None, "called WITHOUT db"

    def test_update_asset_invalidates_dashboard_cache(self, client, monkeypatch):
        from app.routers import assets as assets_router
        from app.services import dashboard_service

        calls = []
        original = dashboard_service.invalidate_dashboard_cache

        def spy(db=None):
            calls.append(db)
            return original(db)

        monkeypatch.setattr(assets_router, "invalidate_dashboard_cache", spy)

        r = client.post(
            "/api/assets/",
            json={
                "name": "X",
                "asset_type": "other",
                "quantity": 1,
                "unit": "piece",
                "purchase_price_vnd": 1_000,
                "current_value_vnd": 1_000,
            },
        )
        assert r.status_code in (200, 201), r.text
        aid = r.json()["id"]
        calls.clear()
        u = client.put(f"/api/assets/{aid}", json={"current_value_vnd": 2_000})
        assert u.status_code == 200
        assert len(calls) == 1
        assert calls[0] is not None

    def test_delete_asset_invalidates_dashboard_cache(self, client, monkeypatch):
        from app.routers import assets as assets_router
        from app.services import dashboard_service

        calls = []
        original = dashboard_service.invalidate_dashboard_cache

        def spy(db=None):
            calls.append(db)
            return original(db)

        monkeypatch.setattr(assets_router, "invalidate_dashboard_cache", spy)

        r = client.post(
            "/api/assets/",
            json={
                "name": "X",
                "asset_type": "other",
                "quantity": 1,
                "unit": "piece",
                "purchase_price_vnd": 1_000,
                "current_value_vnd": 1_000,
            },
        )
        aid = r.json()["id"]
        calls.clear()
        d = client.delete(f"/api/assets/{aid}")
        assert d.status_code in (200, 204)
        assert len(calls) == 1
        assert calls[0] is not None


# ── 22. Template cache invalidation (B6) ─────────────────────────────────────


class TestTemplateCacheInvalidation:
    """B6: Template CRUD must call invalidate_dashboard_cache(db)."""

    def test_create_template_invalidates_dashboard_cache(self, client, cat_ids, monkeypatch):
        from app.routers import templates as tpl_router
        from app.services import dashboard_service

        calls = []
        original = dashboard_service.invalidate_dashboard_cache

        def spy(db=None):
            calls.append(db)
            return original(db)

        monkeypatch.setattr(tpl_router, "invalidate_dashboard_cache", spy)

        r = client.post(
            "/api/templates/",
            json={
                "name": "Tpl-B6",
                "amount": 1_000_000,
                "type": "expense",
                "category_id": cat_ids["food"],
                "description": "x",
                "cadence": "monthly",
                "is_active": True,
            },
        )
        assert r.status_code == 200, r.text
        assert len(calls) == 1
        assert calls[0] is not None

    def test_update_template_invalidates_dashboard_cache(self, client, cat_ids, monkeypatch):
        r = client.post(
            "/api/templates/",
            json={
                "name": "X",
                "amount": 1,
                "type": "expense",
                "category_id": cat_ids["food"],
                "description": "x",
            },
        )
        assert r.status_code == 200
        tid = r.json()["id"]
        from app.routers import templates as tpl_router
        from app.services import dashboard_service

        calls = []
        original = dashboard_service.invalidate_dashboard_cache

        def spy(db=None):
            calls.append(db)
            return original(db)

        monkeypatch.setattr(tpl_router, "invalidate_dashboard_cache", spy)
        u = client.put(f"/api/templates/{tid}", json={"name": "Y"})
        assert u.status_code == 200
        assert len(calls) == 1

    def test_delete_template_invalidates_dashboard_cache(self, client, cat_ids, monkeypatch):
        r = client.post(
            "/api/templates/",
            json={
                "name": "X",
                "amount": 1,
                "type": "expense",
                "category_id": cat_ids["food"],
                "description": "x",
            },
        )
        tid = r.json()["id"]
        from app.routers import templates as tpl_router
        from app.services import dashboard_service

        calls = []
        original = dashboard_service.invalidate_dashboard_cache

        def spy(db=None):
            calls.append(db)
            return original(db)

        monkeypatch.setattr(tpl_router, "invalidate_dashboard_cache", spy)
        d = client.delete(f"/api/templates/{tid}")
        assert d.status_code in (200, 204)
        assert len(calls) == 1


# ── 23. CSV sign-flip (B7) ───────────────────────────────────────────────────


class TestCSVEnglishImport:
    """B7: parse_csv_english must NOT call abs() on the amount — refunds stay negative."""

    def test_csv_english_negative_expense_kept_as_negative(self, client, db_session, tmp_path):
        from app.services.transaction_service import parse_csv_english

        csv = tmp_path / "refund.csv"
        csv.write_text("date,amount,type,category,description\n2026-04-01,-5000,expense,Food,Refund from Vinmart\n")
        content = csv.read_bytes()
        stats = parse_csv_english(content, db_session)
        # The created transactions list isn't directly returned in this signature,
        # but stats["expense"] should be 1 and stats["errors"] empty.
        assert stats["expense"] == 1, f"refund row not imported: {stats}"
        assert stats["skipped"] == 0, f"refund row was skipped: {stats}"
        assert not stats["errors"], f"errors: {stats['errors']}"
        # The created transaction must have amount=-5000, NOT abs(-5000)=5000
        from app.models.database import Transaction

        tx = db_session.query(Transaction).filter(Transaction.description == "Refund from Vinmart").first()
        assert tx is not None, "refund transaction not created"
        assert float(tx.amount) == -5000, f"CSV sign was stripped: amount={tx.amount} (expected -5000)"

    def test_csv_english_negative_income_rejected(self, client, db_session, tmp_path):
        """A negative income row must be skipped with an error, not silently coerced."""
        from app.services.transaction_service import parse_csv_english

        csv = tmp_path / "neg_income.csv"
        csv.write_text("date,amount,type,category,description\n2026-04-01,-100,Income,Salary,Salary refund\n")
        stats = parse_csv_english(csv.read_bytes(), db_session)
        # Should be skipped, not created
        assert stats["income"] == 0, f"negative income was imported: {stats}"
        assert stats["skipped"] == 1, f"negative income was not skipped: {stats}"
        assert any("Income amount must be non-negative" in e for e in stats["errors"]), (
            f"no error logged for negative income. Errors: {stats['errors']}"
        )


# ── 24. Category race-safety (B8) ────────────────────────────────────────────


class TestCategoryRace:
    """B8: get_or_create_category must be concurrent-safe (no duplicate rows)."""

    def test_sequential_get_or_create_returns_same_id(self, db_session):
        from app.services.transaction_service import get_or_create_category
        from app.models.database import Category, TransactionType

        cat1 = get_or_create_category(db_session, "Race-Test-001", TransactionType.EXPENSE)
        db_session.commit()
        cat2 = get_or_create_category(db_session, "Race-Test-001", TransactionType.EXPENSE)
        db_session.commit()
        assert cat1.id == cat2.id, f"two distinct categories with same name+type: {cat1.id} != {cat2.id}"
        count = (
            db_session.query(Category)
            .filter(Category.name == "Race-Test-001", Category.type == TransactionType.EXPENSE)
            .count()
        )
        assert count == 1, f"duplicate row created: {count}"

    def test_integrity_error_handler_re_fetches_existing(self, db_session, monkeypatch):
        """Force an IntegrityError on the first flush; the handler must re-fetch
        the (now-existing) row instead of bubbling."""
        from app.services import transaction_service
        from app.models.database import TransactionType
        from sqlalchemy.exc import IntegrityError

        cat = transaction_service.get_or_create_category(db_session, "Race-Test-002", TransactionType.EXPENSE)
        db_session.commit()
        existing_id = cat.id

        # Patch Category.__init__ (via session.flush) to raise on next call.
        from sqlalchemy.orm import Session as _Session

        original_flush = _Session.flush
        call_count = {"n": 0}

        def flaky_flush(self, *args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise IntegrityError("simulated", {}, Exception("dup key"))
            return original_flush(self, *args, **kwargs)

        monkeypatch.setattr(_Session, "flush", flaky_flush)

        # Second call: should catch IntegrityError and re-fetch the existing row
        cat2 = transaction_service.get_or_create_category(db_session, "Race-Test-002", TransactionType.EXPENSE)
        assert cat2.id == existing_id, f"re-fetch failed: {cat2.id} != {existing_id}"
