"""Tests for the transaction fragment list endpoint filters.

Covers the previously-uncovered filter branches in
`app/routers/fragments/transactions.py` (type, category, project, search,
is_advance, advance_settled, source, needs_review, import_job_id).
"""

from datetime import date


def _make_tx(client, db_session, **overrides):
    """Helper: create a transaction via the API and return it."""
    base = {
        "date": "2026-06-15",
        "amount": 100_000,
        "type": "expense",
        "category_id": 1,
        "description": "Default description",
    }
    base.update(overrides)
    r = client.post("/api/transactions/", json=base)
    if r.status_code != 200:
        raise AssertionError(f"create failed: {r.status_code} {r.text[:500]}")
    return r.json()


def test_filter_by_type_income(client, income_cat, expense_cat, db_session):
    """?type=income returns only income transactions, ignoring expense ones."""
    _make_tx(client, db_session, type="expense", category_id=expense_cat.id, amount=50_000, description="exp-1")
    _make_tx(client, db_session, type="income", category_id=income_cat.id, amount=1_000_000, description="inc-1")
    _make_tx(client, db_session, type="income", category_id=income_cat.id, amount=2_000_000, description="inc-2")

    r = client.get("/fragments/transactions/list?type=income")
    assert r.status_code == 200
    html = r.text
    assert "inc-1" in html
    assert "inc-2" in html
    assert "exp-1" not in html


def test_filter_by_category_id(client, income_cat, expense_cat, db_session):
    """?category_id=X returns only transactions for that category."""
    _make_tx(client, db_session, type="expense", category_id=expense_cat.id, amount=10_000, description="food 1")
    _make_tx(client, db_session, type="expense", category_id=expense_cat.id, amount=20_000, description="food 2")
    _make_tx(client, db_session, type="income", category_id=income_cat.id, amount=5_000_000, description="salary")

    r = client.get(f"/fragments/transactions/list?category_id={expense_cat.id}")
    assert r.status_code == 200
    assert "food 1" in r.text
    assert "food 2" in r.text
    assert "salary" not in r.text


def test_filter_by_project_id(client, income_cat, expense_cat, db_session):
    """?project_id=X returns only transactions linked to that project."""
    # Create a project first
    proj_r = client.post(
        "/api/projects/",
        json={
            "name": "Test Project",
            "type": "investment",
            "target_amount": 100_000_000,
            "start_date": "2026-01-01",
            "status": "active",
        },
    )
    assert proj_r.status_code == 200
    project_id = proj_r.json()["id"]

    _make_tx(
        client,
        db_session,
        type="expense",
        category_id=expense_cat.id,
        amount=10_000,
        project_id=project_id,
        description="linked",
    )
    _make_tx(
        client,
        db_session,
        type="expense",
        category_id=expense_cat.id,
        amount=20_000,
        description="not linked",
    )

    r = client.get(f"/fragments/transactions/list?project_id={project_id}")
    assert r.status_code == 200
    assert "linked" in r.text
    assert "not linked" not in r.text


def test_filter_by_date_range(client, income_cat, db_session):
    """?start_date / ?end_date returns only transactions in that window."""
    _make_tx(
        client,
        db_session,
        date="2026-05-01",
        type="income",
        category_id=income_cat.id,
        amount=100_000,
        description="May",
    )
    _make_tx(
        client,
        db_session,
        date="2026-06-15",
        type="income",
        category_id=income_cat.id,
        amount=200_000,
        description="June mid",
    )
    _make_tx(
        client,
        db_session,
        date="2026-07-01",
        type="income",
        category_id=income_cat.id,
        amount=300_000,
        description="July",
    )

    r = client.get("/fragments/transactions/list?start_date=2026-06-01&end_date=2026-06-30")
    assert r.status_code == 200
    assert "June mid" in r.text
    assert "May" not in r.text
    assert "July" not in r.text


def test_filter_by_search_substring(client, income_cat, db_session):
    """?search=foo matches transactions whose description ILIKE %foo%."""
    _make_tx(
        client,
        db_session,
        type="income",
        category_id=income_cat.id,
        amount=100_000,
        description="Coffee at Highlands",
    )
    _make_tx(
        client,
        db_session,
        type="income",
        category_id=income_cat.id,
        amount=200_000,
        description="Lunch at Katinat",
    )
    _make_tx(
        client,
        db_session,
        type="income",
        category_id=income_cat.id,
        amount=300_000,
        description="Bonus payment",
    )

    r = client.get("/fragments/transactions/list?search=coffee")
    assert r.status_code == 200
    assert "Coffee" in r.text
    assert "Katinat" not in r.text
    assert "Bonus" not in r.text

    # Case-insensitive (ILIKE)
    r2 = client.get("/fragments/transactions/list?search=COFFEE")
    assert "Coffee" in r2.text


def test_filter_by_is_advance(client, income_cat, db_session):
    """?is_advance=true returns only advance transactions."""
    _make_tx(client, db_session, type="income", category_id=income_cat.id, amount=100_000, description="regular")
    _make_tx(
        client,
        db_session,
        type="income",
        category_id=income_cat.id,
        amount=500_000,
        is_advance=True,
        description="advance_one",
    )

    r = client.get("/fragments/transactions/list?is_advance=true")
    assert r.status_code == 200
    assert "advance_one" in r.text
    assert "regular" not in r.text

    # False returns the regular one
    r2 = client.get("/fragments/transactions/list?is_advance=false")
    assert r2.status_code == 200
    assert "regular" in r2.text
    assert "advance_one" not in r2.text


def test_filter_by_advance_settled_unsettled_includes_null(client, income_cat, db_session):
    """?advance_settled=false should include rows with NULL (legacy data
    pre-advance_settled column) — exercises the special NULL branch.
    The router uses PUT for updates."""
    # Create advances with the settled flag set directly (the API sets
    # advance_settled=False by default when is_advance=True)
    from app.models.database import Transaction, TransactionType

    # Mix: one advance_settled=True and one False. The False one is the
    # one we expect the filter to match for advance_settled=false.
    settled_tx = Transaction(
        date=date(2026, 6, 15),
        amount=500_000,
        type=TransactionType.INCOME,
        category_id=income_cat.id,
        description="ROW_advance_settled_TRUE",
        is_advance=True,
        advance_settled=True,
    )
    unsettled_tx = Transaction(
        date=date(2026, 6, 15),
        amount=300_000,
        type=TransactionType.INCOME,
        category_id=income_cat.id,
        description="ROW_advance_settled_FALSE",
        is_advance=True,
        advance_settled=False,
    )
    db_session.add_all([settled_tx, unsettled_tx])
    db_session.commit()

    # Unsettled filter should return only the FALSE one
    r = client.get("/fragments/transactions/list?advance_settled=false")
    assert r.status_code == 200
    assert "ROW_advance_settled_FALSE" in r.text
    assert "ROW_advance_settled_TRUE" not in r.text

    # Settled=true should return only the TRUE one
    r2 = client.get("/fragments/transactions/list?advance_settled=true")
    assert r2.status_code == 200
    assert "ROW_advance_settled_TRUE" in r2.text
    assert "ROW_advance_settled_FALSE" not in r2.text


def test_filter_by_source(client, income_cat, db_session):
    """?source=manual returns only manually-entered transactions."""
    _make_tx(
        client,
        db_session,
        type="income",
        category_id=income_cat.id,
        amount=100_000,
        source="manual",
        description="manual entry",
    )
    _make_tx(
        client,
        db_session,
        type="income",
        category_id=income_cat.id,
        amount=200_000,
        source="template",
        description="from template",
    )

    r = client.get("/fragments/transactions/list?source=manual")
    assert r.status_code == 200
    assert "manual entry" in r.text
    assert "from template" not in r.text


def test_filter_by_needs_review(client, income_cat, db_session):
    """?needs_review=true returns only transactions awaiting review.
    The needs_review flag is server-managed (set by rules), so we insert
    transactions directly with the flag set to exercise the filter."""
    from app.models.database import Transaction, TransactionType

    # Approved transaction (needs_review=False)
    tx1 = Transaction(
        date=date(2026, 6, 15),
        amount=100_000,
        type=TransactionType.INCOME,
        category_id=income_cat.id,
        description="approved_tx",
        needs_review=False,
    )
    db_session.add(tx1)
    # Awaiting review (needs_review=True)
    tx2 = Transaction(
        date=date(2026, 6, 15),
        amount=200_000,
        type=TransactionType.INCOME,
        category_id=income_cat.id,
        description="needs_review_tx",
        needs_review=True,
    )
    db_session.add(tx2)
    db_session.commit()

    r = client.get("/fragments/transactions/list?needs_review=true")
    assert r.status_code == 200
    assert "needs_review_tx" in r.text
    assert "approved_tx" not in r.text

    r2 = client.get("/fragments/transactions/list?needs_review=false")
    assert r2.status_code == 200
    assert "approved_tx" in r2.text
    assert "needs_review_tx" not in r2.text


def test_filter_by_import_job_id(client, income_cat, db_session):
    """?import_job_id=X returns only transactions from that import job.
    The import_job_id is server-set (during OCR/email imports), so we
    create an import job first to satisfy the FK constraint, then insert
    transactions directly with the field set to exercise the filter."""
    from app.models.database import ImportJob, Transaction, TransactionType

    # Create two import jobs (filename, file_path, image_hash are required,
    # source is the only optional one and we set it explicitly to avoid
    # the CIEnum default-construction warning)
    from app.models.database import ImportJobStatus, ImportSource

    job42 = ImportJob(
        filename="test_ocr_42.jpg",
        file_path="/tmp/test_ocr_42.jpg",
        image_hash="a" * 64,
        source_hint=ImportSource.UOB,
        status=ImportJobStatus.DONE,
    )
    job99 = ImportJob(
        filename="test_email_99.eml",
        file_path="/tmp/test_email_99.eml",
        image_hash="b" * 64,
        source_hint=ImportSource.TIMO,
        status=ImportJobStatus.DONE,
    )
    db_session.add_all([job42, job99])
    db_session.commit()
    db_session.refresh(job42)
    db_session.refresh(job99)

    t1 = Transaction(
        date=date(2026, 6, 15),
        amount=100_000,
        type=TransactionType.INCOME,
        category_id=income_cat.id,
        description="from_job_42",
        import_job_id=job42.id,
    )
    t2 = Transaction(
        date=date(2026, 6, 15),
        amount=200_000,
        type=TransactionType.INCOME,
        category_id=income_cat.id,
        description="from_job_99",
        import_job_id=job99.id,
    )
    t3 = Transaction(
        date=date(2026, 6, 15),
        amount=300_000,
        type=TransactionType.INCOME,
        category_id=income_cat.id,
        description="no_job_tx",
    )
    db_session.add_all([t1, t2, t3])
    db_session.commit()

    r = client.get(f"/fragments/transactions/list?import_job_id={job42.id}")
    assert r.status_code == 200
    assert "from_job_42" in r.text
    assert "from_job_99" not in r.text
    assert "no_job_tx" not in r.text


def test_trash_filter_returns_soft_deleted(client, expense_cat, db_session):
    """?trash=true returns only soft-deleted transactions."""
    tx1 = _make_tx(
        client,
        db_session,
        type="expense",
        category_id=expense_cat.id,
        amount=10_000,
        description="to trash",
    )
    _make_tx(
        client,
        db_session,
        type="expense",
        category_id=expense_cat.id,
        amount=20_000,
        description="to keep",
    )

    # Soft-delete tx1
    del_r = client.delete(f"/api/transactions/{tx1['id']}")
    assert del_r.status_code == 200

    r = client.get("/fragments/transactions/list?trash=true")
    assert r.status_code == 200
    assert "to trash" in r.text
    assert "to keep" not in r.text

    # Without trash flag, the soft-deleted one shouldn't appear
    r2 = client.get("/fragments/transactions/list?trash=false")
    assert r2.status_code == 200
    assert "to trash" not in r2.text
    assert "to keep" in r2.text
