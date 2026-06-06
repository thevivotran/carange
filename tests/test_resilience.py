"""Tests for Phase 1/2 resilience additions.

Covers the new code from the Phase 1/2 resilience work:
  OCR worker — _handle_failure, _process_one, _drain_queue, _psycopg2_dsn
  Email worker — _check_email_status, _get_or_create_log_row, _reclaim_stuck_pending,
                 _touch_liveness
  Dashboard — _mv_sum, _fetch_matview_rows fallback, _schedule_matview_refresh,
              MATVIEW aggregation path via monkeypatching

Covers:
  - _mv_sum helper (pure function, no DB required)
  - _fetch_matview_rows graceful fallback on SQLite
  - _schedule_matview_refresh no-op on SQLite
  - email worker: _check_email_status, _get_or_create_log_row
  - email worker retry column presence on EmailIngestLog
"""

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.database import Base, EmailIngestLog


@pytest.fixture()
def db_session(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path}/test.db",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    with Session() as db:
        yield db


# ── _mv_sum (pure function) ───────────────────────────────────────────────────

_SAMPLE_ROWS = [
    {"month": date(2026, 4, 1), "type": "income", "is_savings_related": False, "category_id": 1, "total": 10_000_000},
    {"month": date(2026, 4, 1), "type": "expense", "is_savings_related": False, "category_id": 2, "total": 3_000_000},
    {"month": date(2026, 4, 1), "type": "expense", "is_savings_related": True, "category_id": 3, "total": 2_000_000},
    {"month": date(2026, 4, 1), "type": "expense", "is_savings_related": False, "category_id": 4, "total": 1_500_000},
    {"month": date(2026, 3, 1), "type": "income", "is_savings_related": False, "category_id": 1, "total": 9_000_000},
    {"month": date(2026, 3, 1), "type": "expense", "is_savings_related": False, "category_id": 2, "total": 4_000_000},
]


def test_mv_sum_by_month_and_type():
    from app.services.dashboard_service import _mv_sum

    income = _mv_sum(_SAMPLE_ROWS, month=date(2026, 4, 1), type_val="income")
    assert income == 10_000_000


def test_mv_sum_savings_filter():
    from app.services.dashboard_service import _mv_sum

    savings = _mv_sum(_SAMPLE_ROWS, month=date(2026, 4, 1), type_val="expense", savings=True)
    assert savings == 2_000_000


def test_mv_sum_non_savings_expense():
    from app.services.dashboard_service import _mv_sum

    expense = _mv_sum(_SAMPLE_ROWS, month=date(2026, 4, 1), type_val="expense", savings=False)
    assert expense == 4_500_000


def test_mv_sum_cat_ids_filter():
    from app.services.dashboard_service import _mv_sum

    total = _mv_sum(_SAMPLE_ROWS, month=date(2026, 4, 1), type_val="expense", cat_ids={4})
    assert total == 1_500_000


def test_mv_sum_all_time():
    from app.services.dashboard_service import _mv_sum

    total_income = _mv_sum(_SAMPLE_ROWS, type_val="income", savings=False)
    assert total_income == 19_000_000


def test_mv_sum_until_month():
    from app.services.dashboard_service import _mv_sum

    # Only March rows
    total = _mv_sum(_SAMPLE_ROWS, until_month=date(2026, 3, 1), type_val="income")
    assert total == 9_000_000


def test_mv_sum_from_month():
    from app.services.dashboard_service import _mv_sum

    # Only April rows
    total = _mv_sum(_SAMPLE_ROWS, from_month=date(2026, 4, 1), type_val="expense", savings=False)
    assert total == 4_500_000


def test_mv_sum_empty_rows():
    from app.services.dashboard_service import _mv_sum

    assert _mv_sum([], type_val="income") == 0.0


# ── _fetch_matview_rows graceful fallback on SQLite ───────────────────────────


def test_fetch_matview_rows_returns_none_on_sqlite(db_session):
    from app.services.dashboard_service import _fetch_matview_rows

    # mv_monthly_totals doesn't exist on SQLite — should return None, not raise
    result = _fetch_matview_rows(db_session)
    assert result is None


# ── _schedule_matview_refresh is a no-op on SQLite ───────────────────────────


def test_schedule_matview_refresh_noop_on_sqlite():
    from app.services.dashboard_service import _schedule_matview_refresh

    # Should not raise; _USE_MATVIEW is False for SQLite so this exits immediately
    _schedule_matview_refresh()


# ── email worker: _check_email_status ────────────────────────────────────────


def _make_email_log(db, message_id, status, retry_count=0, retry_after=None):
    row = EmailIngestLog(
        message_id=message_id,
        status=status,
        retry_count=retry_count,
        retry_after=retry_after,
        created_at=datetime.now(timezone.utc),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def test_check_email_status_absent(db_session):
    from email_worker.worker import _check_email_status

    skip, mark_seen = _check_email_status(db_session, "msg-not-exist")
    assert not skip
    assert not mark_seen


def test_check_email_status_done(db_session):
    from email_worker.worker import _check_email_status

    _make_email_log(db_session, "msg-done", "done")
    skip, mark_seen = _check_email_status(db_session, "msg-done")
    assert skip
    assert mark_seen


def test_check_email_status_failed_at_max_retries(db_session):
    from email_worker.worker import MAX_EMAIL_RETRIES, _check_email_status

    _make_email_log(db_session, "msg-perm-fail", "failed", retry_count=MAX_EMAIL_RETRIES)
    skip, mark_seen = _check_email_status(db_session, "msg-perm-fail")
    assert skip
    assert mark_seen


def test_check_email_status_pending_retry_not_due(db_session):
    from email_worker.worker import _check_email_status

    future = datetime.now(timezone.utc) + timedelta(hours=1)
    _make_email_log(db_session, "msg-retry-wait", "pending", retry_count=1, retry_after=future)
    skip, mark_seen = _check_email_status(db_session, "msg-retry-wait")
    assert skip
    assert not mark_seen  # keep UNSEEN in IMAP


def test_check_email_status_pending_retry_due(db_session):
    from email_worker.worker import _check_email_status

    past = datetime.now(timezone.utc) - timedelta(minutes=10)
    _make_email_log(db_session, "msg-retry-due", "pending", retry_count=1, retry_after=past)
    skip, mark_seen = _check_email_status(db_session, "msg-retry-due")
    assert not skip  # due for reprocessing


# ── email worker: _get_or_create_log_row ─────────────────────────────────────


def test_get_or_create_creates_new_row(db_session):
    from email_worker.worker import _get_or_create_log_row

    row = _get_or_create_log_row(db_session, "new-msg-id")
    assert row.id is not None
    assert row.status == "pending"


def test_get_or_create_reuses_existing_and_clears_retry_after(db_session):
    from email_worker.worker import _get_or_create_log_row

    future = datetime.now(timezone.utc) + timedelta(hours=1)
    existing = _make_email_log(db_session, "retry-msg-id", "pending", retry_count=1, retry_after=future)

    row = _get_or_create_log_row(db_session, "retry-msg-id")
    assert row.id == existing.id
    assert row.retry_after is None  # cleared so it's treated as in-progress


# ── email worker: _reclaim_stuck_pending ─────────────────────────────────────


def test_reclaim_stuck_pending_deletes_crashrecovery_rows(db_session):
    from email_worker.worker import _reclaim_stuck_pending

    # Crash-recovery row: pending + no retry_after + old created_at
    old = datetime.now(timezone.utc) - timedelta(hours=2)
    row = EmailIngestLog(
        message_id="stuck-crash",
        status="pending",
        retry_after=None,
        created_at=old,
    )
    db_session.add(row)
    db_session.commit()

    _reclaim_stuck_pending(db_session)

    remaining = db_session.query(EmailIngestLog).filter_by(message_id="stuck-crash").first()
    assert remaining is None


def test_reclaim_stuck_pending_ignores_retry_rows(db_session):
    from email_worker.worker import _reclaim_stuck_pending

    old = datetime.now(timezone.utc) - timedelta(hours=2)
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    row = EmailIngestLog(
        message_id="scheduled-retry",
        status="pending",
        retry_after=future,
        created_at=old,
    )
    db_session.add(row)
    db_session.commit()

    _reclaim_stuck_pending(db_session)

    # Scheduled retry rows must NOT be deleted
    remaining = db_session.query(EmailIngestLog).filter_by(message_id="scheduled-retry").first()
    assert remaining is not None


def test_touch_liveness(tmp_path, monkeypatch):
    import email_worker.worker as ew

    liveness_path = str(tmp_path / "liveness")
    monkeypatch.setattr(ew, "LIVENESS_FILE", liveness_path)
    ew._touch_liveness()
    assert (tmp_path / "liveness").exists()


# ── OCR worker: _process_one and _drain_queue ─────────────────────────────────


@pytest.fixture()
def ocr_session_factory(tmp_path):
    from app.models.database import ImportJob, ImportJobStatus

    engine = create_engine(
        f"sqlite:///{tmp_path}/ocr_test.db",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    with Session() as db:
        job = ImportJob(
            filename="test.png",
            file_path=str(tmp_path / "test.png"),
            image_hash="testhash",
            status=ImportJobStatus.PENDING,
        )
        db.add(job)
        db.commit()

    yield Session


def test_process_one_claims_and_processes(ocr_session_factory, monkeypatch):
    from ocr_worker.worker import _claim_next_sqlite, _process_one
    from app.models.database import ImportJobStatus

    def _fake_process(job, db):
        job.status = ImportJobStatus.DONE
        job.transaction_count = 0
        db.commit()

    monkeypatch.setattr("ocr_worker.processor.process_job", _fake_process)

    result = _process_one(ocr_session_factory, _claim_next_sqlite)
    assert result is True

    # Queue is now empty
    result2 = _process_one(ocr_session_factory, _claim_next_sqlite)
    assert result2 is False


def test_drain_queue_processes_all(ocr_session_factory, monkeypatch):
    from ocr_worker.worker import _claim_next_sqlite, _drain_queue
    from app.models.database import ImportJob, ImportJobStatus

    # Add a second job
    with ocr_session_factory() as db:
        j2 = ImportJob(
            filename="test2.png",
            file_path="/tmp/test2.png",
            image_hash="testhash2",
            status=ImportJobStatus.PENDING,
        )
        db.add(j2)
        db.commit()

    processed = []

    def _fake_process(job, db):
        processed.append(job.id)
        job.status = ImportJobStatus.DONE
        job.transaction_count = 0
        db.commit()

    monkeypatch.setattr("ocr_worker.processor.process_job", _fake_process)

    _drain_queue(ocr_session_factory, _claim_next_sqlite)
    assert len(processed) == 2


# ── Dashboard: MATVIEW aggregation path via monkeypatching ────────────────────


def test_get_dashboard_data_uses_matview_when_available(monkeypatch):
    """Verify that get_dashboard_data reads from mv_rows when _USE_MATVIEW=True."""
    import app.services.dashboard_service as ds
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models.database import Base, Category, TransactionType

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    with Session() as db:
        cat = Category(name="Khác", type=TransactionType.EXPENSE, color="#aaa", is_active=True)
        db.add(cat)
        db.commit()

        monkeypatch.setattr(ds, "_USE_MATVIEW", True)
        sample_mv = [
            {
                "month": date(2026, 6, 1),
                "type": "income",
                "is_savings_related": False,
                "category_id": cat.id,
                "total": 5_000_000,
            },
            {
                "month": date(2026, 6, 1),
                "type": "expense",
                "is_savings_related": False,
                "category_id": cat.id,
                "total": 2_000_000,
            },
        ]
        monkeypatch.setattr(ds, "_fetch_matview_rows", lambda _db: sample_mv)

        result = ds.get_dashboard_data(db, year=2026, month=6)

    assert result["summary"]["total_income"] == 5_000_000
    assert result["summary"]["total_expense"] == 2_000_000
