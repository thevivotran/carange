"""CRUD tests for notes endpoint."""


def test_create_note(client):
    r = client.post("/api/notes/", json={"title": "Test Note", "content": "Hello", "type": "general"})
    assert r.status_code == 200
    d = r.json()
    assert d["title"] == "Test Note"
    assert d["content"] == "Hello"


def test_list_notes(client):
    client.post("/api/notes/", json={"title": "A", "type": "general"})
    client.post("/api/notes/", json={"title": "B", "type": "money_owed"})
    r = client.get("/api/notes/")
    assert r.status_code == 200
    titles = [n["title"] for n in r.json()]
    assert "A" in titles and "B" in titles


def test_update_note_content(client):
    note_id = client.post("/api/notes/", json={"title": "Old", "type": "general"}).json()["id"]
    r = client.put(f"/api/notes/{note_id}", json={"title": "New", "content": "Updated"})
    assert r.status_code == 200
    assert r.json()["title"] == "New"
    assert r.json()["content"] == "Updated"


def test_update_then_list_reflects_change(client):
    """After update, list returns the changed title."""
    note_id = client.post("/api/notes/", json={"title": "Before", "type": "general"}).json()["id"]
    client.put(f"/api/notes/{note_id}", json={"title": "After"})
    titles = [n["title"] for n in client.get("/api/notes/").json()]
    assert "After" in titles
    assert "Before" not in titles


def test_delete_note(client):
    note_id = client.post("/api/notes/", json={"title": "Bye", "type": "general"}).json()["id"]
    assert client.delete(f"/api/notes/{note_id}").status_code == 200
    titles = [n["title"] for n in client.get("/api/notes/").json()]
    assert "Bye" not in titles


def test_delete_nonexistent_note_returns_404(client):
    assert client.delete("/api/notes/999999").status_code == 404


# ── Structured IOUs ───────────────────────────────────────────────────────────


def test_iou_fields_round_trip(client):
    r = client.post(
        "/api/notes/",
        json={
            "title": "Lunch",
            "type": "money_owed",
            "counterparty": "An",
            "amount": 250000,
            "direction": "owed_to_us",
            "due_date": "2026-12-01",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["counterparty"] == "An" and body["amount"] == 250000
    assert body["direction"] == "owed_to_us" and body["due_date"] == "2026-12-01"
    assert body["settled_at"] is None


def test_iou_direction_and_amount_validated(client):
    assert client.post("/api/notes/", json={"title": "x", "direction": "sideways"}).status_code == 422
    assert client.post("/api/notes/", json={"title": "x", "amount": -1}).status_code == 422


def test_iou_summary_counts_only_unsettled_money_owed(client):
    def mk(**kw):
        return client.post("/api/notes/", json={"title": "n", "type": "money_owed", **kw}).json()

    mk(amount=100, direction="owed_to_us")
    mk(amount=50, direction="owed_to_us")
    mk(amount=30, direction="we_owe")
    settled = mk(amount=999, direction="owed_to_us")
    client.post(f"/api/notes/{settled['id']}/settle")
    client.post("/api/notes/", json={"title": "g", "type": "general", "amount": 5000, "direction": "we_owe"})
    assert client.get("/api/notes/summary").json() == {"owed_to_us": 150.0, "we_owe": 30.0}


def test_settle_marks_only_money_owed_and_creates_no_transaction(client, db_session):
    from app.models.database import Transaction

    iou = client.post("/api/notes/", json={"title": "n", "type": "money_owed", "amount": 10}).json()
    r = client.post(f"/api/notes/{iou['id']}/settle")
    assert r.status_code == 200 and r.json()["settled_at"]
    assert db_session.query(Transaction).count() == 0

    general = client.post("/api/notes/", json={"title": "g", "type": "general"}).json()
    assert client.post(f"/api/notes/{general['id']}/settle").status_code == 400
    assert client.post("/api/notes/999999/settle").status_code == 404
