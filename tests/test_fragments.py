"""Tests for HTMX fragment endpoints under /fragments/."""

import pytest
from datetime import date
from unittest.mock import MagicMock, patch
from app.models.database import Transaction, TransactionType, Category


@pytest.fixture()
def category(db_session):
    cat = Category(name="Food", type=TransactionType.EXPENSE, color="#EF4444", icon="utensils")
    db_session.add(cat)
    db_session.commit()
    db_session.refresh(cat)
    return cat


@pytest.fixture()
def sample_transaction(db_session, category):
    tx = Transaction(
        date=date.today(),
        amount=100000,
        type=TransactionType.EXPENSE,
        category_id=category.id,
        description="Lunch",
        source="manual",
    )
    db_session.add(tx)
    db_session.commit()
    db_session.refresh(tx)
    return tx


def test_fragment_list_empty(client):
    r = client.get("/fragments/transactions/list", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "No transactions found" in r.text


def test_fragment_list_with_data(client, sample_transaction):
    r = client.get("/fragments/transactions/list", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "Lunch" in r.text
    assert "₫" in r.text


def test_fragment_list_filter_by_type(client, sample_transaction):
    r = client.get("/fragments/transactions/list?type=income", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "No transactions found" in r.text


def test_fragment_list_trash_mode(client, sample_transaction):
    r = client.get("/fragments/transactions/list?trash=true", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "No transactions found" in r.text  # sample is not deleted


def test_fragment_list_pagination(client):
    r = client.get("/fragments/transactions/list?skip=0&limit=20", headers={"HX-Request": "true"})
    assert r.status_code == 200


def test_fragment_summary(client):
    r = client.get("/fragments/transactions/summary", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "Income" in r.text
    assert "₫" in r.text


def test_fragment_history_no_logs(client, sample_transaction):
    r = client.get(
        f"/fragments/transactions/{sample_transaction.id}/history",
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 200
    assert "No changes recorded" in r.text


def test_fragment_history_nonexistent(client):
    r = client.get("/fragments/transactions/99999/history", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "No changes recorded" in r.text


# ── Dashboard fragment tests ──────────────────────────────────────────────────


def test_fragment_dashboard_safety_score(client):
    r = client.get("/fragments/dashboard/safety-score", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "Family Safety Score" in r.text


def test_fragment_dashboard_safety_score_with_month(client):
    r = client.get("/fragments/dashboard/safety-score?year=2025&month=4", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "Family Safety Score" in r.text


def test_fragment_dashboard_kpi_cards(client):
    r = client.get("/fragments/dashboard/kpi-cards", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "Net Worth" in r.text


def test_fragment_dashboard_kpi_cards_with_month(client):
    r = client.get("/fragments/dashboard/kpi-cards?year=2025&month=4", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "Net Worth" in r.text


def test_settings_page(client):
    r = client.get("/settings")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "savings_target_pct" in r.text


def test_settings_general_post(client):
    r = client.post(
        "/settings/general",
        data={"savings_target_pct": "30", "fi_target_vnd": "", "baby_fund_bundle_id": ""},
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 200
    assert "HX-Trigger" in r.headers


def test_settings_email_post(client):
    r = client.post(
        "/settings/email",
        data={
            "imap_host": "imap.gmail.com",
            "imap_user": "test@test.com",
            "imap_folder": "INBOX",
            "email_poll_interval": "300",
        },  # noqa: E501
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 200
    assert "HX-Trigger" in r.headers


def test_settings_telegram_post(client):
    r = client.post(
        "/settings/telegram",
        data={"telegram_bot_token": "abc123", "telegram_chat_id": "456", "app_url": "http://example.com"},
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 200
    assert "HX-Trigger" in r.headers


def test_settings_telegram_post_checkboxes_roundtrip(client):
    r = client.post(
        "/settings/telegram",
        data={
            "telegram_bot_token": "abc123",
            "telegram_chat_id": "456",
            "app_url": "http://example.com",
            "telegram_hide_amounts": "on",
            "telegram_budget_alerts_enabled": "on",
        },
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 200
    assert "HX-Trigger" in r.headers

    page = client.get("/settings")
    assert page.status_code == 200
    assert 'name="telegram_hide_amounts"' in page.text
    assert 'name="telegram_budget_alerts_enabled"' in page.text
    import re

    hide_match = re.search(r'name="telegram_hide_amounts"[^>]*checked', page.text)
    alerts_match = re.search(r'name="telegram_budget_alerts_enabled"[^>]*checked', page.text)
    assert hide_match, "telegram_hide_amounts checkbox should be checked"
    assert alerts_match, "telegram_budget_alerts_enabled checkbox should be checked"


def test_settings_telegram_test_not_configured(client):
    r = client.post("/settings/telegram/test", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "HX-Trigger" in r.headers


def test_settings_telegram_test_success(client, db_session):
    from app.services.settings_service import set_setting

    set_setting(db_session, "telegram_bot_token", "tok")
    set_setting(db_session, "telegram_chat_id", "123")
    with patch("app.notify.telegram.requests.post") as mock_post:
        mock_post.return_value = MagicMock(ok=True)
        r = client.post("/settings/telegram/test", headers={"HX-Request": "true"})
        assert r.status_code == 200
        assert "HX-Trigger" in r.headers


def test_settings_ocr_post(client):
    r = client.post(
        "/settings/ocr",
        data={"ollama_url": "", "ollama_model": "Qwen3.6-35B-A3B"},
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 200
    assert "HX-Trigger" in r.headers


def test_settings_dashboard_preset_quick_apply(client, db_session, profile_row):
    from app.services.dashboard_layout import get_user_sections

    r = client.post(
        "/settings/dashboard",
        data={"preset": "simple"},
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 200
    assert "HX-Trigger" in r.headers
    assert get_user_sections(db_session, profile_row.id) == frozenset()

    page = client.get("/settings")
    assert 'name="sections"' in page.text


def test_settings_dashboard_post_checkbox_toggles(client, db_session, profile_row):
    from app.services.dashboard_layout import get_user_sections

    r = client.post(
        "/settings/dashboard",
        data={"sections": ["cash_flow", "stress_test"]},
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 200
    assert "HX-Trigger" in r.headers
    assert get_user_sections(db_session, profile_row.id) == frozenset({"cash_flow", "stress_test"})


def test_settings_dashboard_post_invalid_preset_treated_as_toggle_save(client, db_session, profile_row):
    from app.services.dashboard_layout import get_user_sections

    r = client.post(
        "/settings/dashboard",
        data={"preset": "bogus", "sections": ["cash_flow"]},
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 200
    assert get_user_sections(db_session, profile_row.id) == frozenset({"cash_flow"})


def test_settings_dashboard_goals_post(client):
    r = client.post(
        "/settings/dashboard-goals",
        data={"savings_target_pct": "30", "fi_target_vnd": "1000000000", "baby_fund_bundle_id": ""},
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 200
    assert "HX-Trigger" in r.headers

    page = client.get("/settings")
    assert "30" in page.text


def test_settings_navigation_preset_quick_apply(client, db_session, profile_row):
    from app.services.dashboard_layout import NAV_CORE, get_user_nav_items

    r = client.post(
        "/settings/navigation",
        data={"preset": "simple"},
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 200
    assert "HX-Trigger" in r.headers
    assert get_user_nav_items(db_session, profile_row.id) == NAV_CORE


def test_settings_navigation_post_checkbox_toggles(client, db_session, profile_row):
    from app.services.dashboard_layout import NAV_CORE, get_user_nav_items

    r = client.post(
        "/settings/navigation",
        data={"nav_items": ["pulse", "notes", "bogus"]},
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 200
    assert "HX-Trigger" in r.headers
    assert get_user_nav_items(db_session, profile_row.id) == NAV_CORE | {"pulse", "notes"}


def test_settings_thresholds_post(client):
    r = client.post(
        "/settings/thresholds",
        data={
            "review_threshold": "0.9",
            "anomaly_multiplier": "3.0",
            "anomaly_min_samples": "3",
            "stuck_timeout_min": "30",
            "max_retries": "3",
        },
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 200
    assert "HX-Trigger" in r.headers


# ── Budget fragment tests ─────────────────────────────────────────────────────


def test_fragment_budget_rows_default(client):
    r = client.get("/fragments/budget/rows", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "budget-rows-region" in r.text


def test_fragment_budget_rows_explicit_month(client):
    r = client.get("/fragments/budget/rows?year_month=2025-01", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "budget-rows-region" in r.text


def test_fragment_budget_rows_empty(client):
    r = client.get("/fragments/budget/rows?year_month=2000-01", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "No budget set for this month" in r.text


# ── Savings fragment tests ────────────────────────────────────────────────────


def test_fragment_savings_grid_default(client):
    r = client.get("/fragments/savings/grid", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "savings-grid-region" in r.text


def test_fragment_savings_grid_empty(client):
    r = client.get("/fragments/savings/grid?status=active", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "No savings bundles yet" in r.text


def test_fragment_savings_bundle_transactions_empty(client, db_session):
    from datetime import date as _date

    from app.models.database import SavingsBundle, SavingsStatus, SavingsType

    bundle = SavingsBundle(
        name="Test Bundle",
        bank_name="VCB",
        type=SavingsType.FIXED_DEPOSIT,
        initial_deposit=10_000_000,
        current_amount=10_000_000,
        future_amount=10_500_000,
        start_date=_date(2025, 1, 1),
        status=SavingsStatus.ACTIVE,
    )
    db_session.add(bundle)
    db_session.commit()
    db_session.refresh(bundle)
    r = client.get(f"/fragments/savings/{bundle.id}/transactions", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "No linked transactions" in r.text


# ── Projects fragment tests ───────────────────────────────────────────────────


def test_fragment_projects_grid_default(client):
    r = client.get("/fragments/projects/grid", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "projects-grid-region" in r.text


def test_fragment_projects_grid_empty(client):
    r = client.get("/fragments/projects/grid?status=planning", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "No projects yet" in r.text


# ── Categories fragment tests ─────────────────────────────────────────────────


def test_fragment_categories_rows_expense(client):
    r = client.get("/fragments/categories/rows?type=expense", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


def test_fragment_categories_rows_income(client):
    r = client.get("/fragments/categories/rows?type=income", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


def test_fragment_categories_rows_sort(client):
    url = "/fragments/categories/rows?type=expense&sort_col=count&sort_dir=desc"
    r = client.get(url, headers={"HX-Request": "true"})
    assert r.status_code == 200


def test_fragment_categories_rows_empty_type(client):
    r = client.get("/fragments/categories/rows?type=expense", headers={"HX-Request": "true"})
    assert r.status_code == 200


# ── Import fragment tests ─────────────────────────────────────────────────────


def test_fragment_import_jobs_empty(client):
    r = client.get("/fragments/import/jobs", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


def test_fragment_import_jobs_filter(client):
    r = client.get("/fragments/import/jobs?status=done", headers={"HX-Request": "true"})
    assert r.status_code == 200


def test_fragment_import_job_transactions_nonexistent(client):
    r = client.get("/fragments/import/99999/transactions", headers={"HX-Request": "true"})
    assert r.status_code == 200


def test_fragment_import_jobs_search(client):
    r = client.get("/fragments/import/jobs?search=timo", headers={"HX-Request": "true"})
    assert r.status_code == 200


def test_fragment_import_jobs_search_with_data(client, db_session):
    from app.models.database import ImportJob, ImportJobStatus

    job = ImportJob(
        filename="timo_june.jpg",
        file_path="abc123.jpg",
        image_hash="abc" * 21 + "a",
        status=ImportJobStatus.DONE,
        transaction_count=1,
    )
    db_session.add(job)
    db_session.commit()

    r = client.get("/fragments/import/jobs?search=timo", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "timo_june.jpg" in r.text

    r2 = client.get("/fragments/import/jobs?search=uob", headers={"HX-Request": "true"})
    assert r2.status_code == 200
    assert "timo_june.jpg" not in r2.text


def test_fragment_import_jobs_invalid_status(client):
    r = client.get("/fragments/import/jobs?status=bogus", headers={"HX-Request": "true"})
    assert r.status_code == 200


def test_group_by_date_helper():
    from datetime import timedelta, timezone, datetime as dt
    from app.routers.fragments.import_page import _group_by_date

    class Obj:
        def __init__(self, d):
            self.created_at = d

    today = dt.now(timezone.utc)
    yesterday_dt = today - timedelta(days=1)
    old_dt = today - timedelta(days=10)

    items = [Obj(today), Obj(yesterday_dt), Obj(old_dt), Obj(None)]
    groups = _group_by_date(items)
    labels = [label for label, _ in groups]
    assert "Today" in labels
    assert "Yesterday" in labels
    assert "Unknown" in labels
    assert len(groups[0][1]) == 1  # Today: 1 item
    assert len(groups[1][1]) == 1  # Yesterday: 1 item


# ── Templates fragment tests ──────────────────────────────────────────────────


def test_fragment_templates_rows_default(client):
    r = client.get("/fragments/templates/rows", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


def test_fragment_templates_rows_filter(client):
    r = client.get(
        "/fragments/templates/rows?type=expense&is_active=true",
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 200


def test_fragment_templates_rows_empty_category_id_no_error(client):
    # empty string category_id must not return 422
    r = client.get(
        "/fragments/templates/rows?category_id=",
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 200


def test_fragment_templates_rows_is_active_false(client):
    r = client.get(
        "/fragments/templates/rows?is_active=false",
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 200


def test_fragment_templates_rows_with_template(client, db_session):
    from app.models.database import Category, TransactionTemplate, TransactionType

    cat = Category(name="Groceries", type=TransactionType.EXPENSE, color="#EF4444", icon="cart")
    db_session.add(cat)
    db_session.commit()
    db_session.refresh(cat)

    tpl = TransactionTemplate(name="Weekly Groceries", amount=500_000, type=TransactionType.EXPENSE, category_id=cat.id)
    db_session.add(tpl)
    db_session.commit()

    r = client.get(
        f"/fragments/templates/rows?category_id={cat.id}",
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 200
    assert "Weekly Groceries" in r.text


# ── Assets fragment tests ─────────────────────────────────────────────────────


def test_fragment_assets_grid_default(client):
    r = client.get("/fragments/assets/grid", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "assets-grid-region" in r.text


def test_fragment_assets_grid_empty(client):
    r = client.get("/fragments/assets/grid?asset_type=gold", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "No assets yet" in r.text


# ── Import email-logs fragment ────────────────────────────────────────────────


def test_fragment_import_email_logs_empty(client):
    r = client.get("/fragments/import/email-logs", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "No email receipts processed yet" in r.text


def test_fragment_import_email_logs_with_data(client, db_session):
    from datetime import datetime, timezone
    from app.models.database import EmailIngestLog

    log = EmailIngestLog(
        message_id="<test-001@example.com>",
        sender="noreply@timo.vn",
        subject="Timo receipt #123",
        status="done",
        transaction_count=1,
        processed_at=datetime.now(timezone.utc),
    )
    db_session.add(log)
    db_session.commit()
    r = client.get("/fragments/import/email-logs", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "Timo receipt #123" in r.text


def test_fragment_import_email_logs_status_filter(client, db_session):
    from datetime import datetime, timezone
    from app.models.database import EmailIngestLog

    db_session.add(
        EmailIngestLog(
            message_id="<done-01@example.com>",
            sender="a@b.com",
            subject="Done receipt",
            status="done",
            processed_at=datetime.now(timezone.utc),
        )
    )
    db_session.add(
        EmailIngestLog(
            message_id="<fail-01@example.com>",
            sender="a@b.com",
            subject="Failed receipt",
            status="failed",
        )
    )
    db_session.commit()

    r = client.get("/fragments/import/email-logs?status=done", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "Done receipt" in r.text
    assert "Failed receipt" not in r.text


def test_fragment_import_email_logs_search(client, db_session):
    from datetime import datetime, timezone
    from app.models.database import EmailIngestLog

    db_session.add(
        EmailIngestLog(
            message_id="<shopee-01@example.com>",
            sender="receipts@shopee.com",
            subject="Shopee order #999",
            status="done",
            processed_at=datetime.now(timezone.utc),
        )
    )
    db_session.commit()

    r = client.get("/fragments/import/email-logs?search=shopee", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "Shopee order #999" in r.text

    r2 = client.get("/fragments/import/email-logs?search=grab", headers={"HX-Request": "true"})
    assert r2.status_code == 200
    assert "Shopee order #999" not in r2.text


def test_reprocess_email_log_queues_failed_row(client, db_session):
    from app.models.database import EmailIngestLog

    log = EmailIngestLog(
        message_id="<replay-01@example.com>",
        sender="noreply@bank.vn",
        subject="Failed receipt",
        status="failed",
        retry_count=3,
        error_message="Max retries exceeded",
        raw_email=b"x",
        raw_size=1,
    )
    db_session.add(log)
    db_session.commit()

    r = client.post(f"/fragments/import/email-logs/{log.id}/reprocess", headers={"HX-Request": "true"})
    assert r.status_code == 200
    db_session.refresh(log)
    assert log.status == "pending"
    assert log.retry_count == 0
    assert log.retry_after is not None
    assert log.error_message is None


def test_reprocess_email_log_rejected_without_raw(client, db_session):
    from app.models.database import EmailIngestLog

    log = EmailIngestLog(
        message_id="<replay-02@example.com>",
        sender="noreply@bank.vn",
        subject="Old failed receipt",
        status="failed",
    )
    db_session.add(log)
    db_session.commit()

    r = client.post(f"/fragments/import/email-logs/{log.id}/reprocess", headers={"HX-Request": "true"})
    assert r.status_code == 200
    db_session.refresh(log)
    assert log.status == "failed"  # unchanged — nothing stored to replay


def test_reprocess_email_log_rejected_for_committed_row(client, db_session):
    from datetime import datetime, timezone
    from app.models.database import EmailIngestLog

    log = EmailIngestLog(
        message_id="<replay-03@example.com>",
        sender="noreply@bank.vn",
        subject="Committed receipt",
        status="done",
        transaction_count=2,
        processed_at=datetime.now(timezone.utc),
        raw_email=b"x",
        raw_size=1,
    )
    db_session.add(log)
    db_session.commit()

    r = client.post(f"/fragments/import/email-logs/{log.id}/reprocess", headers={"HX-Request": "true"})
    assert r.status_code == 200
    db_session.refresh(log)
    assert log.status == "done"  # would duplicate transactions — refused


# ── Templates has_cadence filter ─────────────────────────────────────────────


def test_fragment_templates_rows_quick_entry(client):
    r = client.get("/fragments/templates/rows?has_cadence=no", headers={"HX-Request": "true"})
    assert r.status_code == 200


def test_fragment_templates_rows_recurring(client):
    r = client.get("/fragments/templates/rows?has_cadence=yes", headers={"HX-Request": "true"})
    assert r.status_code == 200


def test_fragment_templates_rows_cadence_filters_correctly(client, db_session):
    from app.models.database import TransactionTemplate, Category, TransactionType

    cat = Category(name="Auto-Test", type=TransactionType.EXPENSE, color="#000000", icon="circle")
    db_session.add(cat)
    db_session.commit()
    db_session.refresh(cat)

    tpl_quick = TransactionTemplate(name="Quick Only", amount=10000, type=TransactionType.EXPENSE, category_id=cat.id)
    tpl_recur = TransactionTemplate(
        name="Monthly Sub", amount=99000, type=TransactionType.EXPENSE, category_id=cat.id, cadence="monthly"
    )
    db_session.add_all([tpl_quick, tpl_recur])
    db_session.commit()

    r_quick = client.get("/fragments/templates/rows?has_cadence=no", headers={"HX-Request": "true"})
    assert "Quick Only" in r_quick.text
    assert "Monthly Sub" not in r_quick.text

    r_recur = client.get("/fragments/templates/rows?has_cadence=yes", headers={"HX-Request": "true"})
    assert "Monthly Sub" in r_recur.text
    assert "Quick Only" not in r_recur.text


# ── Pulse AI insight parsing helpers ──────────────────────────────────────────


def test_parse_digest_splits_labelled_sections():
    from app.routers.fragments.pulse import _parse_digest

    text = (
        "SUMMARY: Spending fell to 4,478,950 VND this week.\n"
        "NOTABLE: Food dominated at 2,131,310 VND.\n"
        "RECOMMENDATION: Track all 25 transactions next week."
    )
    sections = _parse_digest(text)
    assert [s["label"] for s in sections] == ["Summary", "Notable", "Recommendation"]
    assert sections[0]["tone"] == "blue"
    assert "4,478,950" in sections[0]["text"]


def test_parse_digest_joins_wrapped_continuation_lines():
    from app.routers.fragments.pulse import _parse_digest

    text = "SUMMARY: Line one\ncontinues here.\nRECOMMENDATION: Do the thing."
    sections = _parse_digest(text)
    assert sections[0]["text"] == "Line one continues here."
    assert len(sections) == 2


def test_parse_digest_returns_empty_for_unstructured_or_missing():
    from app.routers.fragments.pulse import _parse_digest

    assert _parse_digest(None) == []
    assert _parse_digest("just a wall of prose with no headers") == []


def test_split_sentences_breaks_prose():
    from app.routers.fragments.pulse import _split_sentences

    text = "You spent 60% of budget. Quich is over by 476,400 VND. Cap it now."
    assert _split_sentences(text) == [
        "You spent 60% of budget.",
        "Quich is over by 476,400 VND.",
        "Cap it now.",
    ]
    assert _split_sentences(None) == []
    assert _split_sentences("   ") == []
