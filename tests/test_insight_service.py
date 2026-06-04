"""Tests for insight_service — upsert, get, and generator functions."""

from datetime import date, datetime, timezone
from unittest.mock import patch

from app.models.database import (
    AIInsight,
    BudgetAllocation,
    Category,
    InsightType,
    Transaction,
    TransactionType,
)
from app.services.insight_service import (
    _build_budget_advisor_prompt,
    _build_weekly_digest_prompt,
    _upsert,
    generate_budget_advisor_sync,
    generate_weekly_digest_sync,
    get_insight,
)


# ── get_insight ───────────────────────────────────────────────────────────────


def test_get_insight_returns_none_when_empty(db_session):
    assert get_insight(db_session, InsightType.WEEKLY_DIGEST) is None


def test_get_insight_returns_row_when_present(db_session):
    db_session.add(
        AIInsight(
            insight_type=InsightType.WEEKLY_DIGEST,
            content="hello",
            generated_at=datetime.now(timezone.utc),
        )
    )
    db_session.commit()
    row = get_insight(db_session, InsightType.WEEKLY_DIGEST)
    assert row is not None
    assert row.content == "hello"


# ── _upsert ───────────────────────────────────────────────────────────────────


def test_upsert_creates_row(db_session):
    _upsert(db_session, InsightType.BUDGET_ADVISOR, "initial")
    row = get_insight(db_session, InsightType.BUDGET_ADVISOR)
    assert row.content == "initial"


def test_upsert_overwrites_existing_row(db_session):
    _upsert(db_session, InsightType.BUDGET_ADVISOR, "first")
    _upsert(db_session, InsightType.BUDGET_ADVISOR, "second")
    assert db_session.query(AIInsight).count() == 1
    assert get_insight(db_session, InsightType.BUDGET_ADVISOR).content == "second"


def test_upsert_stores_trigger_transaction_id(db_session):
    _upsert(db_session, InsightType.BUDGET_ADVISOR, "text", trigger_id=42)
    assert get_insight(db_session, InsightType.BUDGET_ADVISOR).trigger_transaction_id == 42


# ── _build_weekly_digest_prompt ───────────────────────────────────────────────


def test_build_weekly_digest_prompt_returns_none_when_no_expenses(db_session):
    assert _build_weekly_digest_prompt(db_session) is None


def test_build_weekly_digest_prompt_returns_string_with_expenses(db_session):
    cat = Category(name="Food", type=TransactionType.EXPENSE, color="#EF4444", icon="utensils")
    db_session.add(cat)
    db_session.flush()
    db_session.add(
        Transaction(
            date=date.today(),
            amount=100_000,
            type=TransactionType.EXPENSE,
            category_id=cat.id,
            payment_method="cash",
            source="manual",
        )
    )
    db_session.commit()
    prompt = _build_weekly_digest_prompt(db_session)
    assert prompt is not None
    assert "100,000" in prompt


# ── _build_budget_advisor_prompt ──────────────────────────────────────────────


def test_build_budget_advisor_prompt_returns_none_when_no_budget(db_session):
    assert _build_budget_advisor_prompt(db_session) is None


def test_build_budget_advisor_prompt_returns_string_with_budget(db_session):
    today = date.today()
    year_month = f"{today.year:04d}-{today.month:02d}"
    cat = Category(name="Food", type=TransactionType.EXPENSE, color="#EF4444", icon="utensils")
    db_session.add(cat)
    db_session.flush()
    db_session.add(BudgetAllocation(category_id=cat.id, year_month=year_month, amount=2_000_000))
    db_session.commit()
    prompt = _build_budget_advisor_prompt(db_session)
    assert prompt is not None
    assert "Food" in prompt
    assert "Dự báo cuối tháng" in prompt


def test_build_budget_advisor_prompt_includes_transaction_data(db_session):
    today = date.today()
    year_month = f"{today.year:04d}-{today.month:02d}"
    cat = Category(name="Dining", type=TransactionType.EXPENSE, color="#EF4444", icon="utensils")
    db_session.add(cat)
    db_session.flush()
    db_session.add(BudgetAllocation(category_id=cat.id, year_month=year_month, amount=2_000_000))
    tx = Transaction(
        date=today,
        amount=250_000,
        type=TransactionType.EXPENSE,
        category_id=cat.id,
        description="Bữa trưa",
        payment_method="cash",
        source="manual",
    )
    db_session.add(tx)
    db_session.commit()
    prompt = _build_budget_advisor_prompt(db_session)
    assert prompt is not None
    assert "Dining" in prompt


# ── _is_stale ─────────────────────────────────────────────────────────────────


def test_is_stale_returns_false_when_fresh(db_session):
    from app.services.insight_service import _is_stale

    db_session.add(
        AIInsight(
            insight_type=InsightType.WEEKLY_DIGEST,
            content="x",
            generated_at=datetime.now(timezone.utc),
        )
    )
    db_session.commit()
    assert _is_stale(db_session, InsightType.WEEKLY_DIGEST, 12) is False


def test_is_stale_returns_true_when_old(db_session):
    from app.services.insight_service import _is_stale

    db_session.add(
        AIInsight(
            insight_type=InsightType.BUDGET_ADVISOR,
            content="old",
            generated_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
        )
    )
    db_session.commit()
    assert _is_stale(db_session, InsightType.BUDGET_ADVISOR, 2) is True


# ── generate_weekly_digest_sync ───────────────────────────────────────────────


def test_generate_weekly_digest_sync_skips_when_fresh(db_session):
    db_session.add(
        AIInsight(
            insight_type=InsightType.WEEKLY_DIGEST,
            content="cached",
            generated_at=datetime.now(timezone.utc),
        )
    )
    db_session.commit()
    with (
        patch("app.services.insight_service._ollama.is_enabled", return_value=True),
        patch("app.services.insight_service.SessionLocal", return_value=db_session),
        patch("app.services.insight_service._ollama.generate_sync") as mock_gen,
    ):
        generate_weekly_digest_sync()
        mock_gen.assert_not_called()


def test_generate_weekly_digest_sync_skips_when_ollama_disabled():
    with patch("app.services.insight_service._ollama.is_enabled", return_value=False):
        generate_weekly_digest_sync()  # should not raise


def test_generate_weekly_digest_sync_skips_when_no_expenses(db_session):
    with (
        patch("app.services.insight_service._ollama.is_enabled", return_value=True),
        patch("app.services.insight_service.SessionLocal", return_value=db_session),
        patch("app.services.insight_service._ollama.generate_sync") as mock_gen,
    ):
        generate_weekly_digest_sync()
        mock_gen.assert_not_called()


def test_generate_weekly_digest_sync_stores_result(db_session):
    cat = Category(name="Food", type=TransactionType.EXPENSE, color="#EF4444", icon="utensils")
    db_session.add(cat)
    db_session.flush()
    db_session.add(
        Transaction(
            date=date.today(),
            amount=50_000,
            type=TransactionType.EXPENSE,
            category_id=cat.id,
            payment_method="cash",
            source="manual",
        )
    )
    db_session.commit()

    with (
        patch("app.services.insight_service._ollama.is_enabled", return_value=True),
        patch("app.services.insight_service.SessionLocal", return_value=db_session),
        patch("app.services.insight_service._ollama.generate_sync", return_value="Weekly insight."),
    ):
        generate_weekly_digest_sync()

    assert get_insight(db_session, InsightType.WEEKLY_DIGEST).content == "Weekly insight."


def test_generate_weekly_digest_sync_skips_store_when_llm_returns_none(db_session):
    cat = Category(name="Food", type=TransactionType.EXPENSE, color="#EF4444", icon="utensils")
    db_session.add(cat)
    db_session.flush()
    db_session.add(
        Transaction(
            date=date.today(),
            amount=50_000,
            type=TransactionType.EXPENSE,
            category_id=cat.id,
            payment_method="cash",
            source="manual",
        )
    )
    db_session.commit()

    with (
        patch("app.services.insight_service._ollama.is_enabled", return_value=True),
        patch("app.services.insight_service.SessionLocal", return_value=db_session),
        patch("app.services.insight_service._ollama.generate_sync", return_value=None),
    ):
        generate_weekly_digest_sync()

    assert get_insight(db_session, InsightType.WEEKLY_DIGEST) is None


# ── generate_budget_advisor_sync ──────────────────────────────────────────────


def test_generate_budget_advisor_sync_skips_when_fresh(db_session):
    db_session.add(
        AIInsight(
            insight_type=InsightType.BUDGET_ADVISOR,
            content="cached",
            generated_at=datetime.now(timezone.utc),
        )
    )
    db_session.commit()
    with (
        patch("app.services.insight_service._ollama.is_enabled", return_value=True),
        patch("app.services.insight_service.SessionLocal", return_value=db_session),
        patch("app.services.insight_service._ollama.generate_sync") as mock_gen,
    ):
        generate_budget_advisor_sync()
        mock_gen.assert_not_called()


def test_generate_budget_advisor_sync_skips_when_ollama_disabled():
    with patch("app.services.insight_service._ollama.is_enabled", return_value=False):
        generate_budget_advisor_sync()  # should not raise


def test_generate_budget_advisor_sync_skips_when_no_budget(db_session):
    with (
        patch("app.services.insight_service._ollama.is_enabled", return_value=True),
        patch("app.services.insight_service.SessionLocal", return_value=db_session),
        patch("app.services.insight_service._ollama.generate_sync") as mock_gen,
    ):
        generate_budget_advisor_sync()
        mock_gen.assert_not_called()


def test_generate_budget_advisor_sync_stores_result(db_session):
    today = date.today()
    year_month = f"{today.year:04d}-{today.month:02d}"
    cat = Category(name="Food", type=TransactionType.EXPENSE, color="#EF4444", icon="utensils")
    db_session.add(cat)
    db_session.flush()
    db_session.add(BudgetAllocation(category_id=cat.id, year_month=year_month, amount=2_000_000))
    db_session.commit()

    with (
        patch("app.services.insight_service._ollama.is_enabled", return_value=True),
        patch("app.services.insight_service.SessionLocal", return_value=db_session),
        patch("app.services.insight_service._ollama.generate_sync", return_value="Budget insight."),
    ):
        generate_budget_advisor_sync()

    row = get_insight(db_session, InsightType.BUDGET_ADVISOR)
    assert row.content == "Budget insight."
