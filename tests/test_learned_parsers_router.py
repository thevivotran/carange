"""Tests for the learned_parsers admin router (approve + delete).

These exercise the uncovered lines in `app/routers/learned_parsers.py`
(approve_parser and delete_parser endpoints) — the GET / endpoint is
already covered by the existing test_learned_parsers module.
"""

from app.models.database import LearnedParser


def _make_parser(db_session, **overrides) -> LearnedParser:
    """Create a LearnedParser in the test DB. Direct DB session avoids
    needing a `client` fixture + an admin endpoint just to seed."""
    base = {
        "source_name": "test_bank",
        "detection_keywords": ["test"],
        "extraction_script": "def parse(text): return {}",
        "is_approved": False,
    }
    base.update(overrides)
    lp = LearnedParser(**base)
    db_session.add(lp)
    db_session.commit()
    db_session.refresh(lp)
    return lp


def test_approve_parser_marks_as_approved(client, db_session):
    """PATCH /learned-parsers/{id}/approve flips is_approved=True and returns
    the parser id + new flag."""
    lp = _make_parser(db_session, source_name="approve_target")
    assert lp.is_approved is False

    r = client.patch(f"/api/learned-parsers/{lp.id}/approve")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == lp.id
    assert body["is_approved"] is True

    # Verify the DB row was actually updated
    db_session.expire_all()
    fresh = db_session.query(LearnedParser).filter_by(id=lp.id).first()
    assert fresh.is_approved is True


def test_approve_parser_404_when_missing(client):
    """Approving a non-existent parser returns 404 (not 500)."""
    r = client.patch("/api/learned-parsers/999999/approve")
    assert r.status_code == 404
    assert "not found" in r.json()["detail"].lower()


def test_delete_parser_removes_row(client, db_session):
    """DELETE /learned-parsers/{id} hard-deletes the row and returns its id."""
    lp = _make_parser(db_session, source_name="delete_target")
    parser_id = lp.id

    r = client.delete(f"/api/learned-parsers/{parser_id}")
    assert r.status_code == 200
    assert r.json() == {"deleted": parser_id}

    # Verify row is actually gone
    db_session.expire_all()
    fresh = db_session.query(LearnedParser).filter_by(id=parser_id).first()
    assert fresh is None


def test_delete_parser_404_when_missing(client):
    """Deleting a non-existent parser returns 404 (not 500)."""
    r = client.delete("/api/learned-parsers/999999")
    assert r.status_code == 404
    assert "not found" in r.json()["detail"].lower()


def test_approve_then_delete_lifecycle(client, db_session):
    """Approve and then delete the same parser — full lifecycle works."""
    lp = _make_parser(db_session, source_name="lifecycle")

    # Approve
    r1 = client.patch(f"/api/learned-parsers/{lp.id}/approve")
    assert r1.status_code == 200

    # Delete
    r2 = client.delete(f"/api/learned-parsers/{lp.id}")
    assert r2.status_code == 200

    # Both endpoints should now 404 (deletion was hard, not soft)
    r3 = client.patch(f"/api/learned-parsers/{lp.id}/approve")
    assert r3.status_code == 404


def test_list_parsers_excludes_deleted(client, db_session):
    """After a delete, the parser no longer appears in GET /learned-parsers."""
    _make_parser(db_session, source_name="keep_me")
    lp2 = _make_parser(db_session, source_name="delete_me")

    client.delete(f"/api/learned-parsers/{lp2.id}")

    r = client.get("/api/learned-parsers/")
    assert r.status_code == 200
    names = [p["source_name"] for p in r.json()]
    assert "keep_me" in names
    assert "delete_me" not in names
