"""Review inbox: correct amount/date/type before confirming, and view the source screenshot."""

from datetime import date

import pytest

from app.models.database import Category, ImportJob, ImportJobStatus, Transaction, TransactionType


def _cat(db, name, type_=TransactionType.EXPENSE):
    c = Category(name=name, type=type_, color="#111111", icon="tag")
    db.add(c)
    db.commit()
    return c


def _review_tx(db, cat, **kw):
    tx = Transaction(
        date=kw.get("date_val", date(2026, 3, 1)),
        amount=kw.get("amount", 99_000),
        type=kw.get("type_", TransactionType.EXPENSE),
        category_id=cat.id,
        description="OCR read",
        source="ocr",
        confidence_score=kw.get("confidence", 0.5),
        needs_review=True,
        import_job_id=kw.get("import_job_id"),
    )
    db.add(tx)
    db.commit()
    db.refresh(tx)
    return tx


def test_approve_with_corrected_amount_date_and_type(client, db_session):
    food = _cat(db_session, "Food")
    refund = _cat(db_session, "Refund", TransactionType.INCOME)
    tx = _review_tx(db_session, food)
    r = client.post(
        f"/api/review/{tx.id}/approve",
        json={"amount": 1_250_000, "date": "2026-03-04", "type": "income", "category_id": refund.id},
    )
    assert r.status_code == 200
    db_session.refresh(tx)
    assert float(tx.amount) == 1_250_000
    assert tx.date == date(2026, 3, 4)
    assert tx.type == TransactionType.INCOME
    assert tx.category_id == refund.id
    assert tx.needs_review is False


def test_approve_rejects_category_of_the_wrong_type(client, db_session):
    food = _cat(db_session, "Food")
    tx = _review_tx(db_session, food)
    r = client.post(f"/api/review/{tx.id}/approve", json={"type": "income"})
    assert r.status_code == 400
    assert "choose one for income" in r.json()["detail"]
    db_session.refresh(tx)
    assert tx.needs_review is True


def test_approve_rejects_unknown_category(client, db_session):
    food = _cat(db_session, "Food")
    tx = _review_tx(db_session, food)
    assert client.post(f"/api/review/{tx.id}/approve", json={"category_id": 999_999}).status_code == 400


@pytest.fixture
def upload_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("app.routers.import_jobs.UPLOAD_DIR", str(tmp_path))
    return tmp_path


def _job(db, file_path):
    job = ImportJob(
        filename="shot.png", file_path=file_path, image_hash=file_path[-64:].rjust(64, "0"), status=ImportJobStatus.DONE
    )
    db.add(job)
    db.commit()
    return job


def test_import_job_image_served(client, db_session, upload_dir):
    (upload_dir / "abc.png").write_bytes(b"\x89PNG fake")
    job = _job(db_session, "abc.png")
    r = client.get(f"/api/import/jobs/{job.id}/image")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content == b"\x89PNG fake"


def test_import_job_image_missing_file_or_job(client, db_session, upload_dir):
    job = _job(db_session, "gone.png")
    assert client.get(f"/api/import/jobs/{job.id}/image").status_code == 404
    assert client.get("/api/import/jobs/999999/image").status_code == 404


def test_import_job_image_outside_upload_dir_refused(client, db_session, upload_dir, tmp_path_factory):
    outside = tmp_path_factory.mktemp("elsewhere") / "secret.png"
    outside.write_bytes(b"x")
    job = _job(db_session, str(outside))
    assert client.get(f"/api/import/jobs/{job.id}/image").status_code == 404


def test_review_card_has_editable_fields_and_source_link(client, db_session, upload_dir):
    food = _cat(db_session, "Food")
    _cat(db_session, "Salary", TransactionType.INCOME)
    job = _job(db_session, "abc.png")
    tx = _review_tx(db_session, food, import_job_id=job.id)
    html = client.get("/fragments/review/list").text
    assert f'id="review-amount-{tx.id}" data-money' in html
    assert f'id="review-date-{tx.id}"' in html
    assert f'id="review-type-{tx.id}"' in html
    assert f"/api/import/jobs/{job.id}/image" in html
    # income categories are present but hidden for an expense row
    assert 'data-type="income"' in html and "hidden disabled" in html


# ── Bulk confirm ──────────────────────────────────────────────────────────────


def test_bulk_approve_only_confident_live_items(client, db_session):
    from datetime import datetime, timezone

    food = _cat(db_session, "Food")
    sure = _review_tx(db_session, food, confidence=0.95)
    edge = _review_tx(db_session, food, confidence=0.8)
    unsure = _review_tx(db_session, food, confidence=0.4)
    deleted = _review_tx(db_session, food, confidence=0.99)
    deleted.deleted_at = datetime.now(timezone.utc)
    db_session.commit()

    r = client.post("/api/review/approve-bulk", json={"min_confidence": 0.8})
    assert r.status_code == 200
    assert r.json() == {"approved": 2}
    for tx in (sure, edge, unsure, deleted):
        db_session.refresh(tx)
    assert not sure.needs_review and not edge.needs_review
    assert unsure.needs_review and deleted.needs_review


def test_bulk_approve_defaults_to_80_percent(client, db_session):
    food = _cat(db_session, "Food")
    _review_tx(db_session, food, confidence=0.79)
    assert client.post("/api/review/approve-bulk").json() == {"approved": 0}


def test_review_list_offers_high_confidence_confirm(client, db_session):
    food = _cat(db_session, "Food")
    _review_tx(db_session, food, confidence=0.95)
    _review_tx(db_session, food, confidence=0.3)
    html = client.get("/fragments/review/list").text
    assert "Confirm 1 high-confidence" in html
    assert "data-review-card" in html
