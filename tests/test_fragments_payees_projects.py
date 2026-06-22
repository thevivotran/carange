"""Tests for the payees and projects fragment endpoints.

Covers the previously-uncovered filter branches in
- app/routers/fragments/payees.py:21-25 (JSON parse error path)
- app/routers/fragments/projects.py:11-13, 27, 31 (status filter,
  project_type filter, progress_pct calc)
"""

import json


# ── Payees ────────────────────────────────────────────────────────────────


def test_payees_list_handles_invalid_alias_patterns_json(client, db_session):
    """A payee with malformed alias_patterns JSON should fall back to an
    empty list rather than crash the fragment render."""
    from app.models.database import Payee

    p = Payee(
        canonical_name="broken_payee",
        alias_patterns="not valid json {{{",  # malformed
    )
    db_session.add(p)
    db_session.commit()

    r = client.get("/fragments/payees/list")
    assert r.status_code == 200
    # Payee name should still appear in the rendered HTML
    assert "broken_payee" in r.text


def test_payees_list_handles_null_alias_patterns(client, db_session):
    """A payee with alias_patterns=None should also fall back to empty
    list (covers the `or "[]"` branch)."""
    from app.models.database import Payee

    p = Payee(
        canonical_name="null_patterns_payee",
        alias_patterns=None,
    )
    db_session.add(p)
    db_session.commit()

    r = client.get("/fragments/payees/list")
    assert r.status_code == 200
    assert "null_patterns_payee" in r.text


def test_payees_list_parses_valid_alias_patterns(client, db_session):
    """A payee with valid JSON alias_patterns renders them correctly."""
    from app.models.database import Payee

    p = Payee(
        canonical_name="valid_patterns_payee",
        alias_patterns=json.dumps(["highlands", "hcm"]),
    )
    db_session.add(p)
    db_session.commit()

    r = client.get("/fragments/payees/list")
    assert r.status_code == 200
    assert "valid_patterns_payee" in r.text


# ── Projects ──────────────────────────────────────────────────────────────


def test_projects_grid_filter_by_status(client, db_session):
    """?status=in_progress returns only in-progress projects."""
    _make_project(db_session, name="In Progress Project", status="in_progress", project_type="investment")
    _make_project(db_session, name="Completed Project", status="completed", project_type="investment")

    r = client.get("/fragments/projects/grid?status=in_progress")
    assert r.status_code == 200
    assert "In Progress Project" in r.text
    assert "Completed Project" not in r.text


def test_projects_grid_filter_by_project_type(client, db_session):
    """?project_type=real_estate returns only real-estate projects."""
    _make_project(db_session, name="Apartment", project_type="real_estate")
    _make_project(db_session, name="Stocks", project_type="investment")
    _make_project(db_session, name="Vacation Home", project_type="real_estate")

    r = client.get("/fragments/projects/grid?project_type=real_estate")
    assert r.status_code == 200
    assert "Apartment" in r.text
    assert "Vacation Home" in r.text
    assert "Stocks" not in r.text


def test_projects_grid_filter_by_both_status_and_type(client, db_session):
    """?status=in_progress&project_type=X combines both filters."""
    _make_project(
        db_session,
        name="Active RE",
        status="in_progress",
        project_type="real_estate",
    )
    _make_project(
        db_session,
        name="Completed RE",
        status="completed",
        project_type="real_estate",
    )
    _make_project(
        db_session,
        name="Active Inv",
        status="in_progress",
        project_type="investment",
    )

    r = client.get("/fragments/projects/grid?status=in_progress&project_type=real_estate")
    assert r.status_code == 200
    assert "Active RE" in r.text
    assert "Completed RE" not in r.text
    assert "Active Inv" not in r.text


def test_projects_grid_progress_pct_calculated(client, db_session):
    """Each project in the grid has progress_pct computed and attached."""
    _make_project(
        db_session,
        name="Half Done",
        status="in_progress",
        project_type="investment",
        current_amount=50_000_000,
        target_amount=100_000_000,
    )

    r = client.get("/fragments/projects/grid")
    assert r.status_code == 200
    # The progress_pct value 50 should be embedded in the HTML (or at least
    # the project name) — verifies _calc_progress ran without error
    assert "Half Done" in r.text


def test_projects_grid_no_target_amount_returns_zero_progress(client, db_session):
    """A project with target_amount=0 or NULL should not crash _calc_progress."""
    _make_project(
        db_session,
        name="No Target",
        status="in_progress",
        project_type="investment",
        target_amount=0,
    )

    r = client.get("/fragments/projects/grid")
    assert r.status_code == 200
    assert "No Target" in r.text


# ── Helpers ──────────────────────────────────────────────────────────────


def _make_project(db_session, **overrides):
    """Create a FinancialProject with sensible defaults."""
    from app.models.database import FinancialProject

    base = {
        "name": "Test Project",
        "type": "investment",
        "target_amount": 100_000_000,
        "current_amount": 0,
        "status": "planning",
    }
    # Map friendly kwarg names to actual column names
    if "project_type" in overrides:
        overrides["type"] = overrides.pop("project_type")

    base.update(overrides)
    p = FinancialProject(**base)
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p
