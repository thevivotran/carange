"""CRUD tests for Other Assets endpoint."""


def _asset_payload(**overrides):
    base = {
        "name": "USD Cash", "asset_type": "currency",
        "symbol": "USD",
        "quantity": 1000.0, "unit": "USD",
        "purchase_price_vnd": 24_000_000,
        "current_value_vnd": 25_000_000,
    }
    base.update(overrides)
    return base


def test_create_asset(client):
    r = client.post("/api/assets/", json=_asset_payload())
    assert r.status_code == 200
    d = r.json()
    assert d["name"] == "USD Cash"
    assert d["current_value_vnd"] == 25_000_000


def test_create_asset_zero_quantity_rejected(client):
    r = client.post("/api/assets/", json=_asset_payload(quantity=0))
    assert r.status_code == 422


def test_list_assets(client):
    client.post("/api/assets/", json=_asset_payload(name="Gold"))
    client.post("/api/assets/", json=_asset_payload(name="USD"))
    r = client.get("/api/assets/")
    assert r.status_code == 200
    names = [a["name"] for a in r.json()]
    assert "Gold" in names and "USD" in names


def test_get_single_asset(client):
    asset_id = client.post("/api/assets/", json=_asset_payload()).json()["id"]
    r = client.get(f"/api/assets/{asset_id}")
    assert r.status_code == 200
    assert r.json()["id"] == asset_id


def test_get_nonexistent_asset_returns_404(client):
    assert client.get("/api/assets/999999").status_code == 404


def test_update_asset_current_value(client):
    asset_id = client.post("/api/assets/", json=_asset_payload()).json()["id"]
    r = client.put(f"/api/assets/{asset_id}", json={"current_value_vnd": 26_000_000})
    assert r.status_code == 200
    assert r.json()["current_value_vnd"] == 26_000_000


def test_delete_asset(client):
    asset_id = client.post("/api/assets/", json=_asset_payload()).json()["id"]
    assert client.delete(f"/api/assets/{asset_id}").status_code == 200
    assert client.get(f"/api/assets/{asset_id}").status_code == 404


def test_delete_nonexistent_asset_returns_404(client):
    assert client.delete("/api/assets/999999").status_code == 404
