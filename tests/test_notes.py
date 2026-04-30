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
