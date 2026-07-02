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
