"""Integration health: is email import / OCR / Telegram working, from what workers recorded."""

import imaplib
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.models.database import (
    EmailIngestLog,
    ImportJob,
    ImportJobStatus,
    NotificationEvent,
    NotificationEventStatus,
)
from app.services import integration_health as ih
from app.services.settings_service import set_setting

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)


def _by_name(db):
    return {i["name"]: i for i in ih.get_integration_health(db, now=NOW)}


@pytest.fixture(autouse=True)
def _no_env(monkeypatch):
    for key in ("IMAP_USER", "IMAP_PASSWORD", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        monkeypatch.delenv(key, raising=False)


def _email_log(db, mid, status, when, error=None):
    db.add(EmailIngestLog(message_id=mid, status=status, processed_at=when, error_message=error))
    db.commit()


def test_not_configured_when_credentials_missing(db_session):
    h = _by_name(db_session)
    assert h["email"]["status"] == "not_configured"
    assert h["telegram"]["status"] == "not_configured"
    assert h["ocr"]["status"] == "ok"  # nothing uploaded yet is not a failure


def test_ok_warning_error_states(db_session):
    set_setting(db_session, "imap_user", "a@b.c")
    set_setting(db_session, "imap_password", "secret")
    db_session.commit()

    _email_log(db_session, "m1", "done", NOW - timedelta(hours=2))
    assert _by_name(db_session)["email"]["status"] == "ok"

    _email_log(db_session, "m2", "failed", NOW - timedelta(hours=1), "parse error")
    h = _by_name(db_session)["email"]
    assert h["status"] == "warning"  # failed after a success within 24 h
    assert h["last_error"] == "parse error"


def test_error_when_failing_and_no_recent_success(db_session):
    set_setting(db_session, "imap_user", "a@b.c")
    set_setting(db_session, "imap_password", "secret")
    db_session.commit()
    _email_log(db_session, "m1", "done", NOW - timedelta(days=3))
    _email_log(db_session, "m2", "failed", NOW - timedelta(hours=1), "auth")
    assert _by_name(db_session)["email"]["status"] == "error"


def test_ocr_and_telegram_sources(db_session):
    db_session.add(
        ImportJob(
            filename="a.png",
            file_path="a.png",
            image_hash="1" * 64,
            status=ImportJobStatus.FAILED,
            error_message="no text",
            processed_at=NOW - timedelta(hours=1),
        )
    )
    set_setting(db_session, "telegram_bot_token", "t")
    set_setting(db_session, "telegram_chat_id", "1")
    db_session.add(
        NotificationEvent(
            event_type="x", payload={}, status=NotificationEventStatus.DONE, started_at=NOW - timedelta(minutes=5)
        )
    )
    db_session.commit()
    h = _by_name(db_session)
    assert h["ocr"]["status"] == "error" and h["ocr"]["last_error"] == "no text"
    assert h["telegram"]["status"] == "ok"


def test_naive_timestamps_are_treated_as_utc():
    assert ih._aware(datetime(2026, 1, 1)).tzinfo is timezone.utc


def test_any_integration_failing(db_session):
    assert ih.any_integration_failing(db_session) is False


# ── Email connection test ─────────────────────────────────────────────────────


def test_imap_check_requires_credentials(db_session):
    ok, msg = ih.check_imap_login(db_session)
    assert not ok and "app password" in msg


def _configure_email(db):
    set_setting(db, "imap_user", "a@b.c")
    set_setting(db, "imap_password", "secret")
    db.commit()


def test_imap_check_success(db_session):
    _configure_email(db_session)
    conn = MagicMock()
    conn.select.return_value = ("OK", [b"1"])
    with patch.object(ih.imaplib, "IMAP4_SSL", return_value=conn):
        ok, msg = ih.check_imap_login(db_session)
    assert ok and "a@b.c" in msg and "secret" not in msg
    conn.login.assert_called_once_with("a@b.c", "secret")


def test_imap_check_bad_login_and_missing_folder_and_unreachable(db_session):
    _configure_email(db_session)
    conn = MagicMock()
    conn.login.side_effect = imaplib.IMAP4.error("bad")
    with patch.object(ih.imaplib, "IMAP4_SSL", return_value=conn):
        assert ih.check_imap_login(db_session) == (False, "Login failed — check the email address and app password")

    conn = MagicMock()
    conn.select.return_value = ("NO", [])
    conn.logout.side_effect = RuntimeError("ignored")
    with patch.object(ih.imaplib, "IMAP4_SSL", return_value=conn):
        ok, msg = ih.check_imap_login(db_session)
    assert not ok and "folder" in msg

    with patch.object(ih.imaplib, "IMAP4_SSL", side_effect=OSError("down")):
        ok, msg = ih.check_imap_login(db_session)
    assert not ok and "Could not reach" in msg


# ── Endpoints ─────────────────────────────────────────────────────────────────


def test_health_fragment_and_alert(client):
    html = client.get("/settings/integrations/health").text
    assert "Is auto-import working?" in html and "Bank email import" in html
    alert = client.get("/settings/integrations/alert")
    assert alert.status_code == 200
    assert "Auto-import needs attention" not in alert.text
    assert "max-age=60" in alert.headers["cache-control"]


def test_email_test_endpoint_reports_failure_without_secrets(client):
    r = client.post("/settings/email/test")
    assert r.status_code == 200
    assert "app password" in r.headers.get("hx-trigger", "")
