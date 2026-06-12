def test_forecast_data_returns_expected_keys(client):
    resp = client.get("/api/forecast/data")
    assert resp.status_code == 200
    data = resp.json()
    for key in ("series", "events", "low_point", "horizon_net", "shortfall"):
        assert key in data


def test_forecast_data_clamps_horizon(client):
    resp = client.get("/api/forecast/data?horizon=9999")
    assert resp.status_code == 200
    data = resp.json()
    assert data["horizon_days"] == 365


def test_forecast_page_renders(client):
    resp = client.get("/forecast")
    assert resp.status_code == 200
