"""Tests for the shared statistical-outlier detector and its reporting-time reuse."""

from datetime import date

import pytest

from app.models.database import Category, Transaction, TransactionType
from app.services.transaction_service import ANOMALY_MIN_SAMPLES, is_statistical_outlier


def _make_cat(db, name="Food", type_=TransactionType.EXPENSE):
    cat = Category(name=name, type=type_, color="#111111", icon="tag")
    db.add(cat)
    db.commit()
    db.refresh(cat)
    return cat


def _make_tx(db, cat, *, amount, date_val, type_=TransactionType.EXPENSE):
    tx = Transaction(date=date_val, amount=amount, type=type_, category_id=cat.id)
    db.add(tx)
    db.commit()
    db.refresh(tx)
    return tx


class TestIsStatisticalOutlier:
    def test_below_threshold_is_not_an_outlier(self, db_session):
        cat = _make_cat(db_session)
        for i in range(ANOMALY_MIN_SAMPLES):
            _make_tx(db_session, cat, amount=100_000, date_val=date(2026, 1, i + 1))

        assert is_statistical_outlier(db_session, cat.id, TransactionType.EXPENSE, 150_000, date(2026, 2, 1)) is False

    def test_above_threshold_is_an_outlier(self, db_session):
        cat = _make_cat(db_session)
        for i in range(ANOMALY_MIN_SAMPLES):
            _make_tx(db_session, cat, amount=100_000, date_val=date(2026, 1, i + 1))

        # 500,000 > 100,000 * 3.0
        assert is_statistical_outlier(db_session, cat.id, TransactionType.EXPENSE, 500_000, date(2026, 2, 1)) is True

    def test_insufficient_samples_never_flags(self, db_session):
        cat = _make_cat(db_session)
        _make_tx(db_session, cat, amount=100_000, date_val=date(2026, 1, 1))  # only 1 sample

        assert is_statistical_outlier(db_session, cat.id, TransactionType.EXPENSE, 999_999, date(2026, 2, 1)) is False

    def test_exclude_tx_id_removes_the_row_from_its_own_baseline(self, db_session):
        cat = _make_cat(db_session)
        for i in range(ANOMALY_MIN_SAMPLES):
            _make_tx(db_session, cat, amount=100_000, date_val=date(2026, 1, i + 1))
        # A same-day row that would otherwise count toward the baseline.
        big_tx = _make_tx(db_session, cat, amount=500_000, date_val=date(2026, 2, 1))

        # Excluding big_tx's own id: baseline is unaffected by its own amount.
        assert (
            is_statistical_outlier(
                db_session, cat.id, TransactionType.EXPENSE, 500_000, date(2026, 2, 1), exclude_tx_id=big_tx.id
            )
            is True
        )

    def test_future_dated_transactions_do_not_affect_the_baseline(self, db_session):
        """The baseline only looks strictly before as_of_date (trailing average)."""
        cat = _make_cat(db_session)
        for i in range(ANOMALY_MIN_SAMPLES):
            _make_tx(db_session, cat, amount=100_000, date_val=date(2026, 3, i + 1))  # after as_of_date

        # No prior samples before Feb 1 → insufficient samples, never flagged.
        assert is_statistical_outlier(db_session, cat.id, TransactionType.EXPENSE, 999_999, date(2026, 2, 1)) is False


class TestByCategoryReportExcludesOutliers:
    @pytest.fixture()
    def cat_ids(self, client):
        exp = client.post(
            "/api/categories/", json={"name": "Household", "type": "expense", "color": "#EF4444", "icon": "home"}
        ).json()["id"]
        return {"expense": exp}

    def _make_tx(self, client, *, date_str, amount, category_id):
        return client.post(
            "/api/transactions/",
            json={
                "date": date_str,
                "amount": amount,
                "type": "expense",
                "category_id": category_id,
                "description": "",
                "payment_method": "cash",
                "is_savings_related": False,
            },
        )

    def test_total_excluding_outliers_strips_the_lumpy_purchase(self, client, cat_ids):
        # Baseline: routine spend averaging 100k (distinct amounts so the
        # duplicate-transaction guard doesn't collapse same-amount rows).
        for amount in (90_000, 100_000, 110_000):
            self._make_tx(client, date_str="2026-04-10", amount=amount, category_id=cat_ids["expense"])
        # A one-off lumpy purchase far above the 3x trailing average, in the reporting month.
        self._make_tx(client, date_str="2026-05-01", amount=16_000_000, category_id=cat_ids["expense"])

        r = client.get("/api/transactions/stats/by-category?type=expense&year=2026&month=5")
        assert r.status_code == 200
        result = r.json()
        assert len(result) == 1
        row = result[0]
        assert row["total"] == pytest.approx(16_000_000)
        assert row["total_excluding_outliers"] == pytest.approx(0)

    def test_total_excluding_outliers_equals_total_when_no_outliers(self, client, cat_ids):
        self._make_tx(client, date_str="2026-05-01", amount=2_000_000, category_id=cat_ids["expense"])
        self._make_tx(client, date_str="2026-05-02", amount=1_000_000, category_id=cat_ids["expense"])

        r = client.get("/api/transactions/stats/by-category?type=expense&year=2026&month=5")
        assert r.status_code == 200
        row = r.json()[0]
        assert row["total"] == pytest.approx(3_000_000)
        assert row["total_excluding_outliers"] == pytest.approx(3_000_000)
