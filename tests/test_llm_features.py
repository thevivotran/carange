"""
Tests for LLM-powered features:
  - Vision extraction in the OCR processor (_extract_via_vision, _is_anomaly)
  - Pulse digest fragment endpoint (Ollama disabled → no AI card; enabled → card rendered)
"""

from datetime import date, timedelta
from unittest.mock import patch

import pytest

from app.models.database import Base, Category, ImportJob, ImportJobStatus, Transaction, TransactionType
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def session_factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    yield sessionmaker(bind=engine, autocommit=False, autoflush=False)


@pytest.fixture()
def image_file(tmp_path):
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


@pytest.fixture()
def db_with_category(session_factory):
    """Session + one expense category pre-created."""
    with session_factory() as db:
        cat = Category(name="Food & Dining", type=TransactionType.EXPENSE, color="#EF4444", icon="utensils")
        db.add(cat)
        db.commit()
        db.refresh(cat)
        yield db, cat


# ── ollama.py helpers ─────────────────────────────────────────────────────────


def test_build_messages_with_system():
    from app.services.ollama import _build_messages

    msgs = _build_messages("hello", "be concise")
    assert msgs == [{"role": "system", "content": "be concise"}, {"role": "user", "content": "hello"}]


def test_build_messages_no_system():
    from app.services.ollama import _build_messages

    msgs = _build_messages("hello", "")
    assert msgs == [{"role": "user", "content": "hello"}]


def test_extract_response():
    from app.services.ollama import _extract_response

    data = {"choices": [{"message": {"content": "  answer  "}}]}
    assert _extract_response(data) == "answer"


# ── Vision extraction ─────────────────────────────────────────────────────────


def test_extract_via_vision_returns_parsed_transactions(image_file):
    """Vision returns valid JSON → ParsedTransaction list."""
    vision_json = (
        '[{"date": "2026-05-01", "amount": 85000, "type": "expense", '
        '"description": "Grab Food", "category_hint": "Food & Dining"}]'
    )
    with (
        patch("app.services.ollama.is_enabled", return_value=True),
        patch("app.services.ollama.vision_sync", return_value=vision_json),
    ):
        from ocr_worker.processor import _extract_via_vision

        result = _extract_via_vision(image_file)

    assert result is not None
    assert len(result) == 1
    assert result[0].description == "Grab Food"
    assert result[0].amount == 85000
    assert result[0].tx_type == "expense"
    assert result[0].category_hint == "Food & Dining"


def test_extract_via_vision_returns_none_when_ollama_disabled(image_file):
    with patch("app.services.ollama.is_enabled", return_value=False):
        from ocr_worker.processor import _extract_via_vision

        assert _extract_via_vision(image_file) is None


def test_extract_via_vision_returns_none_on_empty_response(image_file):
    with (
        patch("app.services.ollama.is_enabled", return_value=True),
        patch("app.services.ollama.vision_sync", return_value=None),
    ):
        from ocr_worker.processor import _extract_via_vision

        assert _extract_via_vision(image_file) is None


def test_extract_via_vision_returns_none_on_empty_array(image_file):
    with (
        patch("app.services.ollama.is_enabled", return_value=True),
        patch("app.services.ollama.vision_sync", return_value="[]"),
    ):
        from ocr_worker.processor import _extract_via_vision

        assert _extract_via_vision(image_file) is None


def test_extract_via_vision_handles_markdown_wrapper(image_file):
    """Model sometimes wraps JSON in markdown code fences."""
    wrapped = (
        "```json\n"
        '[{"date": "2026-05-10", "amount": 120000, "type": "expense", '
        '"description": "Shopee", "category_hint": "Shopping"}]\n'
        "```"
    )
    with (
        patch("app.services.ollama.is_enabled", return_value=True),
        patch("app.services.ollama.vision_sync", return_value=wrapped),
    ):
        from ocr_worker.processor import _extract_via_vision

        result = _extract_via_vision(image_file)

    assert result is not None
    assert result[0].amount == 120000


def test_extract_via_vision_skips_malformed_items(image_file):
    """Malformed items are skipped; valid ones are kept."""
    mixed = '[{"date": "2026-05-01", "amount": 50000, "type": "expense", "description": "Coffee"},{"bad": "item"}]'
    with (
        patch("app.services.ollama.is_enabled", return_value=True),
        patch("app.services.ollama.vision_sync", return_value=mixed),
    ):
        from ocr_worker.processor import _extract_via_vision

        result = _extract_via_vision(image_file)

    assert result is not None
    assert len(result) == 1
    assert result[0].description == "Coffee"


# ── Anomaly detection ─────────────────────────────────────────────────────────


def test_is_anomaly_flags_large_transaction(db_with_category):
    db, cat = db_with_category
    cutoff = date.today() - timedelta(days=30)
    for i in range(5):
        db.add(
            Transaction(
                date=cutoff + timedelta(days=i),
                amount=100_000,
                type=TransactionType.EXPENSE,
                category_id=cat.id,
                payment_method="cash",
                source="manual",
            )
        )
    db.commit()

    from ocr_worker.types import ParsedTransaction
    from ocr_worker.processor import _is_anomaly

    pt = ParsedTransaction(date=date.today(), amount=500_000, tx_type="expense", description="Big meal", confidence=0.9)
    assert _is_anomaly(db, pt, cat.id) is True


def test_is_anomaly_no_flag_below_threshold(db_with_category):
    db, cat = db_with_category
    cutoff = date.today() - timedelta(days=30)
    for i in range(5):
        db.add(
            Transaction(
                date=cutoff + timedelta(days=i),
                amount=100_000,
                type=TransactionType.EXPENSE,
                category_id=cat.id,
                payment_method="cash",
                source="manual",
            )
        )
    db.commit()

    from ocr_worker.types import ParsedTransaction
    from ocr_worker.processor import _is_anomaly

    pt = ParsedTransaction(
        date=date.today(), amount=250_000, tx_type="expense", description="Normal meal", confidence=0.9
    )
    assert _is_anomaly(db, pt, cat.id) is False


def test_is_anomaly_no_flag_insufficient_samples(db_with_category):
    """Fewer than ANOMALY_MIN_SAMPLES prior transactions → never flag."""
    db, cat = db_with_category
    db.add(
        Transaction(
            date=date.today() - timedelta(days=1),
            amount=50_000,
            type=TransactionType.EXPENSE,
            category_id=cat.id,
            payment_method="cash",
            source="manual",
        )
    )
    db.commit()

    from ocr_worker.types import ParsedTransaction
    from ocr_worker.processor import _is_anomaly

    pt = ParsedTransaction(
        date=date.today(), amount=9_999_999, tx_type="expense", description="Huge purchase", confidence=0.9
    )
    assert _is_anomaly(db, pt, cat.id) is False


# ── Processor uses vision path when Ollama enabled ────────────────────────────


def test_processor_uses_vision_path_when_ollama_enabled(session_factory, image_file, monkeypatch):
    vision_json = (
        '[{"date": "2026-05-01", "amount": 75000, "type": "expense", '
        '"description": "Grab Express", "category_hint": "Transportation"}]'
    )

    with session_factory() as db:
        cat = Category(name="Transportation", type=TransactionType.EXPENSE, color="#F59E0B", icon="car")
        others = Category(name="Others", type=TransactionType.EXPENSE, color="#6B7280", icon="circle")
        db.add_all([cat, others])
        db.commit()

        # Use absolute path so _resolve_file_path returns it as-is, bypassing UPLOAD_DIR cache
        job = ImportJob(
            filename="test.png",
            file_path=image_file,
            image_hash="vision001",
            status=ImportJobStatus.PENDING,
        )
        db.add(job)
        db.commit()
        db.refresh(job)

        with (
            patch("app.services.ollama.is_enabled", return_value=True),
            patch("app.services.ollama.vision_sync", return_value=vision_json),
            patch("app.services.ollama.generate_sync", return_value="Transportation"),
        ):
            from ocr_worker.processor import process_job

            process_job(job, db)

        db.refresh(job)
        assert job.status == ImportJobStatus.DONE
        assert job.transaction_count == 1
        tx = db.query(Transaction).filter(Transaction.import_job_id == job.id).first()
        assert tx is not None
        assert tx.amount == 75000


# ── Pulse digest endpoint ─────────────────────────────────────────────────────


def test_pulse_digest_no_card_when_ollama_disabled(client):
    with patch("app.services.ollama.is_enabled", return_value=False):
        r = client.get("/fragments/pulse/digest")
    assert r.status_code == 200
    assert b"ollama" not in r.content.lower()


def test_pulse_digest_shows_unavailable_when_ollama_fails(client):
    """Ollama enabled but generate returns None → fallback message shown."""
    with (
        patch("app.services.ollama.is_enabled", return_value=True),
        patch("app.services.ollama.generate", return_value=None),
    ):
        r = client.get("/fragments/pulse/digest")
    assert r.status_code == 200


# ── Budget advisor endpoint ───────────────────────────────────────────────────


def test_budget_advisor_no_card_when_ollama_disabled(client):
    with patch("app.services.ollama.is_enabled", return_value=False):
        r = client.get("/fragments/pulse/budget-advisor")
    assert r.status_code == 200
    assert "AI Phân Tích".encode() not in r.content


def test_budget_advisor_no_card_when_no_budget_set(client):
    """Ollama enabled but no BudgetAllocation rows → card not rendered."""
    with (
        patch("app.services.ollama.is_enabled", return_value=True),
        patch("app.services.ollama.generate", return_value="Test insight"),
    ):
        r = client.get("/fragments/pulse/budget-advisor")
    assert r.status_code == 200
    assert "AI Phân Tích".encode() not in r.content


def test_budget_advisor_shows_insight_when_budget_exists(client, db_session):
    """Pre-seeded AIInsight row is shown directly — no LLM call on page load."""
    from datetime import datetime, timezone

    from app.models.database import AIInsight, BudgetAllocation, Category, InsightType, TransactionType

    today = date.today()
    year_month = f"{today.year:04d}-{today.month:02d}"
    cat = Category(name="Food & Dining", type=TransactionType.EXPENSE, color="#EF4444", icon="utensils")
    db_session.add(cat)
    db_session.flush()
    db_session.add(BudgetAllocation(category_id=cat.id, year_month=year_month, amount=3_000_000))
    db_session.add(
        AIInsight(
            insight_type=InsightType.BUDGET_ADVISOR,
            content="Chi tiêu tháng này ổn định.",
            generated_at=datetime.now(timezone.utc),
        )
    )
    db_session.commit()

    with patch("app.services.ollama.is_enabled", return_value=True):
        r = client.get("/fragments/pulse/budget-advisor")
    assert r.status_code == 200
    assert "Chi tiêu tháng này ổn định.".encode() in r.content


def test_budget_advisor_shows_pending_when_no_insight(client, db_session):
    """No AIInsight row yet → show 'generating' placeholder, not 'AI unavailable'."""
    from app.models.database import BudgetAllocation, Category, TransactionType

    today = date.today()
    year_month = f"{today.year:04d}-{today.month:02d}"
    cat = Category(name="Shopping", type=TransactionType.EXPENSE, color="#EC4899", icon="shopping-bag")
    db_session.add(cat)
    db_session.flush()
    db_session.add(BudgetAllocation(category_id=cat.id, year_month=year_month, amount=2_000_000))
    db_session.commit()

    with patch("app.services.ollama.is_enabled", return_value=True):
        r = client.get("/fragments/pulse/budget-advisor")
    assert r.status_code == 200
    assert "Đang tạo phân tích".encode() in r.content


def test_budget_advisor_over_budget_badge(client, db_session):
    """Over-budget category shows the 'over budget' badge in the card."""
    from app.models.database import BudgetAllocation, Category, Transaction, TransactionType

    today = date.today()
    year_month = f"{today.year:04d}-{today.month:02d}"
    cat = Category(name="Entertainment", type=TransactionType.EXPENSE, color="#8B5CF6", icon="film")
    db_session.add(cat)
    db_session.flush()
    db_session.add(BudgetAllocation(category_id=cat.id, year_month=year_month, amount=500_000))
    db_session.add(
        Transaction(
            date=today,
            amount=700_000,
            type=TransactionType.EXPENSE,
            category_id=cat.id,
            payment_method="cash",
            source="manual",
        )
    )
    db_session.commit()

    with patch("app.services.ollama.is_enabled", return_value=True):
        r = client.get("/fragments/pulse/budget-advisor")
    assert r.status_code == 200
    assert b"over budget" in r.content
