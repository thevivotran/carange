"""
Integration tests for the OCR worker skeleton.
Runs against the shared PostgreSQL test database (see tests/conftest.py).
"""

import os
import pytest
from sqlalchemy.orm import sessionmaker

from app.models.database import ImportJob, ImportJobStatus, ImportSource


@pytest.fixture()
def session_factory(db_session):
    return sessionmaker(bind=db_session.get_bind(), autocommit=False, autoflush=False)


@pytest.fixture()
def image_file(tmp_path):
    """Minimal valid 1×1 PNG."""
    import struct
    import zlib

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
    raw = zlib.compress(b"\x00\xff\xff\xff")
    idat = struct.pack(">I", len(raw)) + b"IDAT" + raw + struct.pack(">I", zlib.crc32(b"IDAT" + raw) & 0xFFFFFFFF)
    iend = b"\x00\x00\x00\x00IEND\xaeB\x60\x82"
    p = tmp_path / "test.png"
    p.write_bytes(sig + ihdr + idat + iend)
    return str(p)


def _make_job(db, file_path, source_hint=None):
    job = ImportJob(
        filename="test.png",
        file_path=file_path,
        image_hash="abc123",
        source_hint=source_hint,
        status=ImportJobStatus.PENDING,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def test_processor_marks_done_when_image_exists(session_factory, image_file, monkeypatch):
    monkeypatch.setenv("UPLOAD_DIR", os.path.dirname(image_file))
    monkeypatch.setattr("ocr_worker.ocr.extract_blocks", lambda _p: [])  # no paddleocr needed
    from ocr_worker.processor import process_job

    with session_factory() as db:
        job = _make_job(db, image_file)
        process_job(job, db)
        db.refresh(job)

        assert job.status == ImportJobStatus.DONE
        assert job.transaction_count == 0
        assert job.processed_at is not None


def test_processor_marks_failed_when_image_missing(session_factory, tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    from ocr_worker.processor import process_job

    with session_factory() as db:
        job = _make_job(db, str(tmp_path / "nonexistent.png"))
        process_job(job, db)
        db.refresh(job)

        assert job.status == ImportJobStatus.FAILED
        assert job.error_message is not None
        assert "not found" in job.error_message.lower()


def test_processor_copies_source_hint_to_detected(session_factory, image_file, monkeypatch):
    monkeypatch.setenv("UPLOAD_DIR", os.path.dirname(image_file))
    monkeypatch.setattr("ocr_worker.ocr.extract_blocks", lambda _p: [])  # no paddleocr needed
    from ocr_worker.processor import process_job

    with session_factory() as db:
        job = _make_job(db, image_file, source_hint=ImportSource.GRAB)
        process_job(job, db)
        db.refresh(job)

        assert job.detected_source == ImportSource.GRAB


def test_worker_claim_sets_processing(session_factory, image_file):
    from ocr_worker.worker import _claim_next_pg as _claim_next

    with session_factory() as db:
        _make_job(db, image_file)

    with session_factory() as db:
        job = _claim_next(db)
        assert job is not None
        assert job.status == ImportJobStatus.PROCESSING


def test_worker_claim_returns_none_when_empty(session_factory):
    from ocr_worker.worker import _claim_next_pg as _claim_next

    with session_factory() as db:
        result = _claim_next(db)
        assert result is None


def test_worker_claim_skips_non_pending(session_factory, image_file):
    """Processing and done jobs should not be re-claimed."""
    from ocr_worker.worker import _claim_next_pg as _claim_next

    with session_factory() as db:
        job = _make_job(db, image_file)
        job.status = ImportJobStatus.DONE
        db.commit()

    with session_factory() as db:
        result = _claim_next(db)
        assert result is None


def test_worker_claim_sets_started_at(session_factory, image_file):
    from ocr_worker.worker import _claim_next_pg as _claim_next

    with session_factory() as db:
        _make_job(db, image_file)

    with session_factory() as db:
        job = _claim_next(db)
        assert job.started_at is not None


def test_handle_failure_retries(session_factory, image_file):
    from ocr_worker.worker import _handle_failure

    with session_factory() as db:
        job = _make_job(db, image_file)
        job.status = ImportJobStatus.PROCESSING
        db.commit()
        _handle_failure(db, job, "transient error")
        db.refresh(job)
        assert job.status == ImportJobStatus.PENDING
        assert job.retry_count == 1
        assert job.retry_after is not None


def test_handle_failure_permanent_after_max_retries(session_factory, image_file):
    from ocr_worker.worker import _handle_failure, MAX_RETRIES

    with session_factory() as db:
        job = _make_job(db, image_file)
        job.status = ImportJobStatus.PROCESSING
        job.retry_count = MAX_RETRIES
        db.commit()
        _handle_failure(db, job, "still broken")
        db.refresh(job)
        assert job.status == ImportJobStatus.FAILED
        assert job.processed_at is not None


def test_psycopg2_dsn_strips_driver_prefix():
    from ocr_worker.worker import _psycopg2_dsn

    assert _psycopg2_dsn("postgresql+psycopg2://user:pw@host/db") == "postgresql://user:pw@host/db"
    assert _psycopg2_dsn("postgres://user:pw@host/db") == "postgresql://user:pw@host/db"
    assert _psycopg2_dsn("postgresql://user:pw@host/db") == "postgresql://user:pw@host/db"


def test_worker_claim_skips_retry_after_in_future(session_factory, image_file):
    from datetime import datetime, timedelta, timezone
    from ocr_worker.worker import _claim_next_pg as _claim_next

    with session_factory() as db:
        job = _make_job(db, image_file)
        job.retry_after = datetime.now(timezone.utc) + timedelta(hours=1)
        db.commit()

    with session_factory() as db:
        result = _claim_next(db)
        assert result is None


def test_ocr_exception_schedules_retry_and_keeps_image(session_factory, image_file, monkeypatch):
    """A transient OCR failure must go back to PENDING with backoff, and the
    image must stay on disk so the retry has something to process."""
    monkeypatch.setenv("UPLOAD_DIR", os.path.dirname(image_file))

    def _boom(_path):
        raise RuntimeError("paddle exploded")

    monkeypatch.setattr("ocr_worker.ocr.extract_blocks", _boom)
    from ocr_worker.worker import _claim_next_pg, _process_one

    with session_factory() as db:
        job = _make_job(db, image_file)
        job_id = job.id

    assert _process_one(session_factory, _claim_next_pg) is True

    with session_factory() as db:
        job = db.query(ImportJob).filter(ImportJob.id == job_id).first()
        assert job.status == ImportJobStatus.PENDING
        assert job.retry_count == 1
        assert job.retry_after is not None
        assert "paddle exploded" in job.error_message
    assert os.path.isfile(image_file)


def test_handle_failure_permanent_deletes_image(session_factory, image_file, monkeypatch):
    monkeypatch.setenv("UPLOAD_DIR", os.path.dirname(image_file))
    from ocr_worker.worker import _handle_failure, MAX_RETRIES

    with session_factory() as db:
        job = _make_job(db, image_file)
        job.status = ImportJobStatus.PROCESSING
        job.retry_count = MAX_RETRIES
        db.commit()
        _handle_failure(db, job, "still broken")
        db.refresh(job)
        assert job.status == ImportJobStatus.FAILED
    assert not os.path.isfile(image_file)


def test_stuck_reclaim_increments_retry_count(session_factory, image_file):
    from datetime import datetime, timedelta, timezone
    from ocr_worker.worker import _claim_next_pg as _claim_next

    with session_factory() as db:
        job = _make_job(db, image_file)
        job.status = ImportJobStatus.PROCESSING
        job.started_at = datetime.now(timezone.utc) - timedelta(hours=2)
        db.commit()

    with session_factory() as db:
        reclaimed = _claim_next(db)
        assert reclaimed is not None
        assert reclaimed.status == ImportJobStatus.PROCESSING
        assert reclaimed.retry_count == 1


def test_stuck_reclaim_fails_permanently_after_max_retries(session_factory, image_file, monkeypatch):
    monkeypatch.setenv("UPLOAD_DIR", os.path.dirname(image_file))
    from datetime import datetime, timedelta, timezone
    from ocr_worker.worker import _claim_next_pg as _claim_next, MAX_RETRIES

    with session_factory() as db:
        job = _make_job(db, image_file)
        job.status = ImportJobStatus.PROCESSING
        job.started_at = datetime.now(timezone.utc) - timedelta(hours=2)
        job.retry_count = MAX_RETRIES
        db.commit()
        job_id = job.id

    with session_factory() as db:
        assert _claim_next(db) is None
        job = db.query(ImportJob).filter(ImportJob.id == job_id).first()
        assert job.status == ImportJobStatus.FAILED
        assert job.processed_at is not None
        assert "stuck" in job.error_message.lower()
    assert not os.path.isfile(image_file)


def test_vision_empty_array_marks_done_without_ocr_fallback(session_factory, image_file, monkeypatch):
    """When the vision model explicitly reports no transactions, the job is
    done — PaddleOCR + GenericParser must NOT run (it would hallucinate)."""
    monkeypatch.setenv("UPLOAD_DIR", os.path.dirname(image_file))

    def _no_fallback(_path):
        raise AssertionError("PaddleOCR fallback must not run")

    monkeypatch.setattr("ocr_worker.ocr.extract_blocks", _no_fallback)
    monkeypatch.setattr("app.services.ollama.is_enabled", lambda: True)
    monkeypatch.setattr("app.services.ollama.vision_sync", lambda *a, **kw: "[]")
    from ocr_worker.processor import process_job

    with session_factory() as db:
        job = _make_job(db, image_file)
        process_job(job, db)
        db.refresh(job)

        assert job.status == ImportJobStatus.DONE
        assert job.transaction_count == 0


def test_low_conf_triggers_vision_fallback(session_factory, image_file, monkeypatch):
    monkeypatch.setenv("UPLOAD_DIR", os.path.dirname(image_file))
    from datetime import date as _date

    from ocr_worker.types import ParsedTransaction, TextBlock

    blocks = [TextBlock(text="low conf", confidence=0.9, x=0, y=0, w=10, h=10)]

    low_conf_txn = ParsedTransaction(
        date=_date.today(),
        amount=50000,
        tx_type="expense",
        description="test",
        confidence=0.5,
    )

    monkeypatch.setattr("ocr_worker.ocr.extract_blocks", lambda _p: blocks)
    monkeypatch.setattr("app.services.ollama.is_enabled", lambda: True)

    vision_called = []

    def _vision(fp):
        vision_called.append(True)
        return []

    monkeypatch.setattr("ocr_worker.processor._extract_via_vision", _vision)

    from ocr_worker.parsers.generic import GenericParser

    monkeypatch.setattr("ocr_worker.processor.get_parser", lambda _s: GenericParser())
    monkeypatch.setattr("ocr_worker.parsers.generic.GenericParser.parse", lambda _s, _b: [low_conf_txn])

    from ocr_worker.processor import process_job

    with session_factory() as db:
        job = _make_job(db, image_file)
        process_job(job, db)
        db.refresh(job)

    assert vision_called, "vision fallback should have been called"
    assert job.status == ImportJobStatus.DONE


def test_learned_parser_lookup_commits_and_returns(session_factory, image_file, monkeypatch):
    monkeypatch.setenv("UPLOAD_DIR", os.path.dirname(image_file))
    from datetime import date as _date

    from ocr_worker.types import ParsedTransaction, TextBlock

    blocks = [TextBlock(text="special bank keyword", confidence=0.9, x=0, y=0, w=10, h=10)]
    monkeypatch.setattr("ocr_worker.ocr.extract_blocks", lambda _p: blocks)
    monkeypatch.setattr("app.services.ollama.is_enabled", lambda: False)

    learned_txn = ParsedTransaction(
        date=_date.today(),
        amount=100000,
        tx_type="income",
        description="learned",
        confidence=0.9,
    )

    def _fake_lookup(db, full_text):
        if "special bank keyword" in full_text:

            class _LP:
                source_name = "special_bank"
                extraction_script = "stub"

            return _LP()
        return None

    monkeypatch.setattr("ocr_worker.learned_parser_store.lookup", _fake_lookup)
    monkeypatch.setattr("ocr_worker.learned_parser_store.run_parser", lambda _s, _b: [learned_txn])

    detect_called = []
    monkeypatch.setattr("ocr_worker.processor.detect_source", lambda _b: detect_called.append(True) or None)

    from ocr_worker.processor import process_job

    with session_factory() as db:
        job = _make_job(db, image_file)
        process_job(job, db)
        db.refresh(job)

    assert not detect_called, "detect_source should not be called when learned parser matches"
    assert job.status == ImportJobStatus.DONE


def test_vision_fallback_failure_uses_original_parse(session_factory, image_file, monkeypatch):
    monkeypatch.setenv("UPLOAD_DIR", os.path.dirname(image_file))
    from datetime import date as _date

    from ocr_worker.types import ParsedTransaction, TextBlock

    blocks = [TextBlock(text="fallback fail", confidence=0.9, x=0, y=0, w=10, h=10)]
    monkeypatch.setattr("ocr_worker.ocr.extract_blocks", lambda _p: blocks)
    monkeypatch.setattr("app.services.ollama.is_enabled", lambda: True)
    monkeypatch.setattr("ocr_worker.processor._extract_via_vision", lambda _fp: None)

    original_txn = ParsedTransaction(
        date=_date.today(),
        amount=25000,
        tx_type="expense",
        description="original",
        confidence=0.5,
    )

    from ocr_worker.parsers.generic import GenericParser

    monkeypatch.setattr("ocr_worker.processor.get_parser", lambda _s: GenericParser())
    monkeypatch.setattr("ocr_worker.parsers.generic.GenericParser.parse", lambda _s, _b: [original_txn])

    from ocr_worker.processor import process_job

    with session_factory() as db:
        job = _make_job(db, image_file)
        process_job(job, db)
        db.refresh(job)

    assert job.status == ImportJobStatus.DONE


def test_high_conf_parse_skips_vision_fallback(session_factory, image_file, monkeypatch):
    monkeypatch.setenv("UPLOAD_DIR", os.path.dirname(image_file))
    from datetime import date as _date

    from ocr_worker.types import ParsedTransaction, TextBlock

    blocks = [TextBlock(text="high conf", confidence=0.9, x=0, y=0, w=10, h=10)]

    high_conf_txn = ParsedTransaction(
        date=_date.today(),
        amount=50000,
        tx_type="expense",
        description="test",
        confidence=0.9,
    )

    monkeypatch.setattr("ocr_worker.ocr.extract_blocks", lambda _p: blocks)
    monkeypatch.setattr("app.services.ollama.is_enabled", lambda: True)

    vision_call_count = []

    def _vision(fp):
        vision_call_count.append(True)
        if len(vision_call_count) == 1:
            return None
        return []

    monkeypatch.setattr("ocr_worker.processor._extract_via_vision", _vision)

    from ocr_worker.parsers.generic import GenericParser

    monkeypatch.setattr("ocr_worker.processor.get_parser", lambda _s: GenericParser())
    monkeypatch.setattr("ocr_worker.parsers.generic.GenericParser.parse", lambda _s, _b: [high_conf_txn])

    from ocr_worker.processor import process_job

    with session_factory() as db:
        job = _make_job(db, image_file)
        process_job(job, db)
        db.refresh(job)

    assert len(vision_call_count) == 1, "vision should only be called once (initial attempt), not for low-conf fallback"
    assert job.status == ImportJobStatus.DONE


def test_unknown_source_triggers_learned_parser_generation(session_factory, image_file, monkeypatch):
    monkeypatch.setenv("UPLOAD_DIR", os.path.dirname(image_file))
    import json

    from ocr_worker.types import TextBlock

    blocks = [TextBlock(text="mystery bank tx 100", confidence=0.9, x=0, y=0, w=10, h=10)]
    monkeypatch.setattr("ocr_worker.ocr.extract_blocks", lambda _p: blocks)
    monkeypatch.setattr("app.services.ollama.is_enabled", lambda: True)
    monkeypatch.setattr("ocr_worker.processor.detect_source", lambda _b: None)

    generated_response = json.dumps(
        {
            "transactions": [
                {
                    "date": "2026-05-15",
                    "amount": 100000,
                    "type": "expense",
                    "description": "mystery tx",
                    "category_hint": "Others",
                }
            ],
            "parser": {
                "source_name": "mystery_bank",
                "detection_keywords": ["mystery bank"],
                "extraction_script": "def parse(blocks):\n    return []",
            },
        }
    )

    vision_call_count = []

    def _vision_sync(*a, **kw):
        vision_call_count.append(True)
        if len(vision_call_count) == 1:
            return None
        return generated_response

    monkeypatch.setattr("app.services.ollama.vision_sync", _vision_sync)

    save_called = []

    def _fake_save(db, source_name, keywords, script):
        save_called.append(
            {
                "source_name": source_name,
                "keywords": keywords,
                "script": script,
            }
        )

    monkeypatch.setattr("ocr_worker.learned_parser_store.save", _fake_save)
    monkeypatch.setattr("ocr_worker.learned_parser_store.run_parser", lambda _s, _b: [])

    from ocr_worker.processor import process_job

    with session_factory() as db:
        job = _make_job(db, image_file)
        process_job(job, db)
        db.refresh(job)

    assert save_called, "learned_parser_store.save should have been called"
    assert save_called[0]["source_name"] == "mystery_bank"
    assert "def parse(" in save_called[0]["script"]
    assert job.status == ImportJobStatus.DONE
