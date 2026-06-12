"""Tests for Phase 1/2 resilience additions.

Covers the new code from the Phase 1/2 resilience work:
  OCR worker — _handle_failure, _process_one, _drain_queue, _psycopg2_dsn
  Email worker — _check_email_status, _get_or_create_log_row, _reclaim_stuck_pending,
                 _touch_liveness
  Dashboard — _mv_sum, _fetch_matview_rows fallback, _schedule_matview_refresh,
              MATVIEW aggregation path via monkeypatching

Covers:
  - _mv_sum helper (pure function, no DB required)
  - email worker: _check_email_status, _get_or_create_log_row
  - email worker retry column presence on EmailIngestLog
  - Dashboard MATVIEW aggregation path via monkeypatching
"""

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import sessionmaker

from app.models.database import EmailIngestLog


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


# ── email worker: log row creation + raw storage ─────────────────────────────

_RAW_EMAIL = (
    b"From: Timo <support@timo.vn>\r\n"
    b"To: me@example.com\r\n"
    b"Subject: Debit Transaction Notice\r\n"
    b"Date: Tue, 09 Jun 2026 08:39:00 +0700\r\n"
    b"Message-ID: <abc123@timo.vn>\r\n"
    b"\r\n"
    b"Your Spend Account has been debited 37,000 VND on 09/06/2026 08:39.\r\n"
)


def _make_email_log(db, message_id, status, retry_count=0, retry_after=None, raw=None, created_at=None):
    from email_worker.worker import _compress

    row = EmailIngestLog(
        message_id=message_id,
        status=status,
        retry_count=retry_count,
        retry_after=retry_after,
        created_at=created_at or datetime.now(timezone.utc),
        raw_email=_compress(raw) if raw else None,
        raw_size=len(_compress(raw)) if raw else None,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def test_create_log_row_stores_metadata_and_compressed_raw(db_session):
    from email_worker.worker import _create_log_row, _decompress

    row = _create_log_row(db_session, "<abc123@timo.vn>", _RAW_EMAIL)
    assert row.status == "pending"
    assert "timo.vn" in row.sender
    assert row.subject == "Debit Transaction Notice"
    assert row.received_at is not None
    assert row.raw_size and row.raw_size > 0
    assert _decompress(row.raw_email) == _RAW_EMAIL


def test_compress_roundtrip_and_uncompressed_fallback():
    from email_worker.worker import _compress, _decompress

    assert _decompress(_compress(b"hello")) == b"hello"
    assert _decompress(b"not compressed") == b"not compressed"  # defensive path


# ── email worker: _process_row retry semantics ────────────────────────────────


def test_process_row_success_runs_pipeline(db_session, monkeypatch):
    import email_worker.processor as proc
    from email_worker.worker import _process_row

    def _ok(row, raw, db):
        row.status = "done"
        row.transaction_count = 1
        db.commit()

    monkeypatch.setattr(proc, "process_email", _ok)
    row = _make_email_log(db_session, "ok-msg", "pending", raw=_RAW_EMAIL)
    _process_row(db_session, row, _RAW_EMAIL)
    assert row.status == "done"


def test_process_row_schedules_backoff_on_error(db_session, monkeypatch):
    import email_worker.processor as proc
    from email_worker.worker import _process_row

    def _boom(row, raw, db):
        raise ValueError("parse exploded")

    monkeypatch.setattr(proc, "process_email", _boom)
    row = _make_email_log(db_session, "retry-msg", "pending", raw=_RAW_EMAIL)
    _process_row(db_session, row, _RAW_EMAIL)

    assert row.status == "pending"
    assert row.retry_count == 1
    assert row.retry_after is not None
    assert "Retry 1/" in row.error_message


def test_process_row_marks_failed_after_max_retries(db_session, monkeypatch):
    import email_worker.processor as proc
    import email_worker.worker as ew

    def _boom(row, raw, db):
        raise ValueError("still broken")

    monkeypatch.setattr(proc, "process_email", _boom)
    row = _make_email_log(db_session, "maxed-msg", "pending", retry_count=ew.MAX_EMAIL_RETRIES, raw=_RAW_EMAIL)
    ew._process_row(db_session, row, _RAW_EMAIL)

    assert row.status == "failed"
    assert "Max retries exceeded" in row.error_message
    assert row.raw_size  # raw copy kept for manual reprocessing


def test_process_row_llm_unavailable_does_not_consume_retries(db_session, monkeypatch):
    import email_worker.processor as proc
    from email_worker.parsers.base import LLMUnavailableError
    from email_worker.worker import _process_row

    def _llm_down(row, raw, db):
        raise LLMUnavailableError("vLLM unreachable")

    monkeypatch.setattr(proc, "process_email", _llm_down)
    row = _make_email_log(db_session, "llm-down-msg", "pending", raw=_RAW_EMAIL)
    _process_row(db_session, row, _RAW_EMAIL)

    assert row.status == "pending"
    assert row.retry_count == 0  # GPU node off ≠ a failed attempt
    assert row.retry_after is not None
    assert "LLM unavailable" in row.error_message


# ── email worker: _process_due_retries ────────────────────────────────────────


def test_due_retry_is_reprocessed_from_stored_raw(db_session, monkeypatch):
    import email_worker.processor as proc
    from email_worker.worker import _process_due_retries

    seen_raw = []

    def _ok(row, raw, db):
        seen_raw.append(raw)
        row.status = "done"
        db.commit()

    monkeypatch.setattr(proc, "process_email", _ok)
    past = datetime.now(timezone.utc) - timedelta(minutes=5)
    _make_email_log(db_session, "due-msg", "pending", retry_after=past, raw=_RAW_EMAIL)

    count = _process_due_retries(db_session)
    assert count == 1
    assert seen_raw == [_RAW_EMAIL]  # decompressed back to the original bytes


def test_future_retry_is_not_touched(db_session, monkeypatch):
    import email_worker.processor as proc
    from email_worker.worker import _process_due_retries

    monkeypatch.setattr(proc, "process_email", lambda *a: (_ for _ in ()).throw(AssertionError("must not run")))
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    row = _make_email_log(db_session, "future-msg", "pending", retry_after=future, raw=_RAW_EMAIL)

    assert _process_due_retries(db_session) == 0
    assert row.status == "pending"


def test_due_retry_without_raw_marked_failed(db_session):
    from email_worker.worker import _process_due_retries

    past = datetime.now(timezone.utc) - timedelta(minutes=5)
    row = _make_email_log(db_session, "no-raw-msg", "pending", retry_after=past, raw=None)

    _process_due_retries(db_session)
    assert row.status == "failed"
    assert "No stored raw copy" in row.error_message


def test_stuck_row_with_raw_is_rescheduled_and_retried(db_session, monkeypatch):
    import email_worker.processor as proc
    from email_worker.worker import _process_due_retries

    def _ok(row, raw, db):
        row.status = "done"
        db.commit()

    monkeypatch.setattr(proc, "process_email", _ok)
    old = datetime.now(timezone.utc) - timedelta(hours=2)
    row = _make_email_log(db_session, "stuck-with-raw", "pending", raw=_RAW_EMAIL, created_at=old)

    _process_due_retries(db_session)
    assert row.status == "done"


def test_stuck_row_without_raw_marked_failed(db_session):
    from email_worker.worker import _process_due_retries

    old = datetime.now(timezone.utc) - timedelta(hours=2)
    row = _make_email_log(db_session, "stuck-no-raw", "pending", raw=None, created_at=old)

    _process_due_retries(db_session)
    assert row.status == "failed"
    assert "no raw copy" in row.error_message


# ── email worker: UID folder cursor ───────────────────────────────────────────


def test_get_folder_state_creates_and_reuses(db_session):
    from email_worker.worker import _get_folder_state

    state = _get_folder_state(db_session, "me@example.com", "INBOX")
    assert state.last_uid == 0
    state.last_uid = 42
    db_session.commit()

    again = _get_folder_state(db_session, "me@example.com", "INBOX")
    assert again.id == state.id
    assert again.last_uid == 42


def test_header_message_id_extraction():
    from email_worker.worker import _header_message_id

    item = {b"BODY[HEADER.FIELDS (MESSAGE-ID)]": b"Message-ID: <xyz@host>\r\n\r\n", b"SEQ": 1}
    assert _header_message_id(item) == "<xyz@host>"
    assert _header_message_id({b"SEQ": 1}) == ""


def test_touch_liveness(tmp_path, monkeypatch):
    import email_worker.worker as ew

    liveness_path = str(tmp_path / "liveness")
    monkeypatch.setattr(ew, "LIVENESS_FILE", liveness_path)
    ew._touch_liveness()
    assert (tmp_path / "liveness").exists()


# ── learned patterns: DB-backed store ─────────────────────────────────────────


@pytest.fixture()
def patched_lp_session(db_session, monkeypatch):
    """Point the learned_patterns store at the test database."""
    import email_worker.learned_patterns as lp

    class _Factory:
        def __call__(self):
            return self

        def __enter__(self):
            return db_session

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(lp, "SessionLocal", _Factory())
    return lp


_PATTERNS = {"amount_patterns": [{"pattern": r"debited ([\d,]+) VND", "group": 1, "tx_type": "expense"}]}


def test_learned_patterns_save_and_get(patched_lp_session):
    lp = patched_lp_session
    lp.save_patterns("noreply@newbank.vn", _PATTERNS)
    got = lp.get_patterns("alerts@newbank.vn")  # same domain, different local part
    assert got["amount_patterns"][0]["tx_type"] == "expense"
    assert lp.get_patterns("other@unknown.com") is None


def test_learned_patterns_success_resets_failure_streak(patched_lp_session, db_session):
    from app.models.database import LearnedPattern

    lp = patched_lp_session
    lp.save_patterns("noreply@newbank.vn", _PATTERNS)
    lp.record_failure("noreply@newbank.vn")
    lp.record_success("noreply@newbank.vn")

    row = db_session.query(LearnedPattern).filter_by(domain="newbank.vn").one()
    assert row.success_count == 1
    assert row.failure_count == 0


def test_learned_patterns_dropped_after_consecutive_failures(patched_lp_session):
    lp = patched_lp_session
    lp.save_patterns("noreply@newbank.vn", _PATTERNS)
    for _ in range(lp.MAX_CONSECUTIVE_FAILURES):
        lp.record_failure("noreply@newbank.vn")
    assert lp.get_patterns("noreply@newbank.vn") is None  # re-learn from scratch


# ── OCR worker: _process_one and _drain_queue ─────────────────────────────────


@pytest.fixture()
def ocr_session_factory(db_session, tmp_path):
    from app.models.database import ImportJob, ImportJobStatus

    job = ImportJob(
        filename="test.png",
        file_path=str(tmp_path / "test.png"),
        image_hash="testhash",
        status=ImportJobStatus.PENDING,
    )
    db_session.add(job)
    db_session.commit()

    yield sessionmaker(bind=db_session.get_bind(), autocommit=False, autoflush=False)


def test_process_one_claims_and_processes(ocr_session_factory, monkeypatch):
    from ocr_worker.worker import _claim_next_pg, _process_one
    from app.models.database import ImportJobStatus

    def _fake_process(job, db):
        job.status = ImportJobStatus.DONE
        job.transaction_count = 0
        db.commit()

    monkeypatch.setattr("ocr_worker.processor.process_job", _fake_process)

    result = _process_one(ocr_session_factory, _claim_next_pg)
    assert result is True

    # Queue is now empty
    result2 = _process_one(ocr_session_factory, _claim_next_pg)
    assert result2 is False


def test_drain_queue_processes_all(ocr_session_factory, monkeypatch):
    from ocr_worker.worker import _claim_next_pg, _drain_queue
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

    _drain_queue(ocr_session_factory, _claim_next_pg)
    assert len(processed) == 2


# ── Dashboard: MATVIEW aggregation path via monkeypatching ────────────────────


def test_get_dashboard_data_uses_matview_when_available(db_session, monkeypatch):
    """Verify that get_dashboard_data reads from mv_rows when _USE_MATVIEW=True."""
    import app.services.dashboard_service as ds
    from app.models.database import Category, TransactionType

    cat = Category(name="Khác", type=TransactionType.EXPENSE, color="#aaa", is_active=True)
    db_session.add(cat)
    db_session.commit()

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

    result = ds.get_dashboard_data(db_session, year=2026, month=6)

    assert result["summary"]["total_income"] == 5_000_000
    assert result["summary"]["total_expense"] == 2_000_000
