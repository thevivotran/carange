"""Extended category tests — duplicate guard, filters, merge, toggle-active."""

from datetime import date

from app.models.database import Category as CatModel, Transaction as TxModel, TransactionType


# ── Duplicate guard ───────────────────────────────────────────────────────────


def test_create_duplicate_category_returns_400(client):
    payload = {"name": "UniqueFood", "type": "expense", "color": "#EF4444", "icon": "utensils"}
    client.post("/api/categories/", json=payload)
    r = client.post("/api/categories/", json=payload)
    assert r.status_code == 400
    assert "already exists" in r.json()["detail"]


def test_create_same_name_different_type_succeeds(client):
    client.post("/api/categories/", json={"name": "Transfer", "type": "expense", "color": "#aaa", "icon": "x"})
    r = client.post("/api/categories/", json={"name": "Transfer", "type": "income", "color": "#bbb", "icon": "x"})
    assert r.status_code == 200


# ── Update guards ─────────────────────────────────────────────────────────────


def test_update_nonexistent_category_returns_404(client):
    r = client.put("/api/categories/999999", json={"name": "Ghost", "type": "expense", "color": "#aaa", "icon": "x"})
    assert r.status_code == 404


def test_update_category_name_collision_returns_400(client):
    client.post("/api/categories/", json={"name": "CatA", "type": "expense", "color": "#aaa", "icon": "x"})
    cat_b_id = client.post(
        "/api/categories/", json={"name": "CatB", "type": "expense", "color": "#bbb", "icon": "x"}
    ).json()["id"]
    r = client.put(
        f"/api/categories/{cat_b_id}", json={"name": "CatA", "type": "expense", "color": "#ccc", "icon": "x"}
    )
    assert r.status_code == 400
    assert "already exists" in r.json()["detail"]


# ── Filters ───────────────────────────────────────────────────────────────────


def test_list_categories_filter_by_type(client):
    client.post("/api/categories/", json={"name": "FilterInc", "type": "income", "color": "#aaa", "icon": "x"})
    client.post("/api/categories/", json={"name": "FilterExp", "type": "expense", "color": "#bbb", "icon": "x"})
    r = client.get("/api/categories/?type=income")
    assert r.status_code == 200
    assert all(c["type"] == "income" for c in r.json())


def test_list_categories_filter_by_is_active(client, db_session):
    active = CatModel(name="ActiveCat", type=TransactionType.EXPENSE, color="#aaa", icon="x", is_active=True)
    inactive = CatModel(name="InactiveCat", type=TransactionType.EXPENSE, color="#bbb", icon="x", is_active=False)
    db_session.add_all([active, inactive])
    db_session.commit()

    r = client.get("/api/categories/?is_active=true")
    assert r.status_code == 200
    assert all(c["is_active"] is True for c in r.json())

    r2 = client.get("/api/categories/?is_active=false")
    assert r2.status_code == 200
    assert all(c["is_active"] is False for c in r2.json())


# ── Delete guard ──────────────────────────────────────────────────────────────


def test_delete_category_with_transactions_returns_400(client, db_session):
    cat = CatModel(name="HasTx", type=TransactionType.EXPENSE, color="#aaa", icon="x")
    db_session.add(cat)
    db_session.commit()
    db_session.refresh(cat)

    tx = TxModel(date=date(2026, 5, 1), amount=100, type=TransactionType.EXPENSE, category_id=cat.id)
    db_session.add(tx)
    db_session.commit()

    r = client.delete(f"/api/categories/{cat.id}")
    assert r.status_code == 400
    assert "transactions" in r.json()["detail"]


# ── Merge ─────────────────────────────────────────────────────────────────────


def test_merge_category_moves_transactions(client, db_session):
    src = CatModel(name="MergeSrc", type=TransactionType.EXPENSE, color="#aaa", icon="x")
    tgt = CatModel(name="MergeTgt", type=TransactionType.EXPENSE, color="#bbb", icon="x")
    db_session.add_all([src, tgt])
    db_session.commit()
    db_session.refresh(src)
    db_session.refresh(tgt)

    tx = TxModel(date=date(2026, 5, 1), amount=500, type=TransactionType.EXPENSE, category_id=src.id)
    db_session.add(tx)
    db_session.commit()

    r = client.post(f"/api/categories/{src.id}/merge-into/{tgt.id}")
    assert r.status_code == 200
    d = r.json()
    assert d["moved"] == 1

    # Source deleted
    assert client.get(f"/api/categories/{src.id}").status_code == 404
    # Transaction moved to target
    tgt_detail = client.get(f"/api/categories/{tgt.id}").json()
    assert tgt_detail["transaction_count"] == 1


def test_merge_category_nonexistent_source_returns_404(client, db_session):
    tgt = CatModel(name="TgtOnly", type=TransactionType.EXPENSE, color="#bbb", icon="x")
    db_session.add(tgt)
    db_session.commit()
    db_session.refresh(tgt)
    r = client.post(f"/api/categories/999999/merge-into/{tgt.id}")
    assert r.status_code == 404


def test_merge_category_nonexistent_target_returns_404(client, db_session):
    src = CatModel(name="SrcOnly", type=TransactionType.EXPENSE, color="#aaa", icon="x")
    db_session.add(src)
    db_session.commit()
    db_session.refresh(src)
    r = client.post(f"/api/categories/{src.id}/merge-into/999999")
    assert r.status_code == 404


def test_merge_category_type_mismatch_returns_400(client, db_session):
    src = CatModel(name="SrcTypeMismatch", type=TransactionType.EXPENSE, color="#aaa", icon="x")
    tgt = CatModel(name="TgtTypeMismatch", type=TransactionType.INCOME, color="#bbb", icon="x")
    db_session.add_all([src, tgt])
    db_session.commit()
    db_session.refresh(src)
    db_session.refresh(tgt)
    r = client.post(f"/api/categories/{src.id}/merge-into/{tgt.id}")
    assert r.status_code == 400


# ── Toggle active ─────────────────────────────────────────────────────────────


def test_toggle_category_active_deactivates(client):
    cat_id = client.post(
        "/api/categories/", json={"name": "ToToggle", "type": "expense", "color": "#aaa", "icon": "x"}
    ).json()["id"]
    r = client.patch(f"/api/categories/{cat_id}/toggle-active")
    assert r.status_code == 200
    assert r.json()["is_active"] is False


def test_toggle_category_active_reactivates(client):
    cat_id = client.post(
        "/api/categories/", json={"name": "ToReactivate", "type": "expense", "color": "#aaa", "icon": "x"}
    ).json()["id"]
    client.patch(f"/api/categories/{cat_id}/toggle-active")  # deactivate
    r = client.patch(f"/api/categories/{cat_id}/toggle-active")  # reactivate
    assert r.status_code == 200
    assert r.json()["is_active"] is True


def test_toggle_nonexistent_category_returns_404(client):
    r = client.patch("/api/categories/999999/toggle-active")
    assert r.status_code == 404
