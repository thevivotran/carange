"""Extended asset tests — filter by type, stats summary, update 404."""

import pytest


def _asset(client, **overrides):
    base = {
        "name": "USD Cash",
        "asset_type": "currency",
        "symbol": "USD",
        "quantity": 1000.0,
        "unit": "USD",
        "purchase_price_vnd": 24_000_000,
        "current_value_vnd": 25_000_000,
    }
    base.update(overrides)
    r = client.post("/api/assets/", json=base)
    assert r.status_code == 200
    return r.json()


def test_list_assets_filter_by_asset_type(client):
    _asset(client, name="Gold Bar", asset_type="gold")
    _asset(client, name="USD", asset_type="currency")
    r = client.get("/api/assets/?asset_type=gold")
    assert r.status_code == 200
    assert all(a["asset_type"] == "gold" for a in r.json())


def test_assets_stats_summary_empty(client):
    r = client.get("/api/assets/stats/summary")
    assert r.status_code == 200
    d = r.json()
    assert d["total_assets_count"] == 0
    assert d["total_invested_vnd"] == pytest.approx(0)
    assert d["total_current_value_vnd"] == pytest.approx(0)
    assert d["total_gain_loss_pct"] == pytest.approx(0)


def test_assets_stats_summary_with_data(client):
    _asset(client, name="A1", purchase_price_vnd=10_000_000, current_value_vnd=12_000_000)
    _asset(client, name="A2", purchase_price_vnd=5_000_000, current_value_vnd=4_000_000)
    r = client.get("/api/assets/stats/summary")
    assert r.status_code == 200
    d = r.json()
    assert d["total_assets_count"] == 2
    assert d["total_invested_vnd"] == pytest.approx(15_000_000)
    assert d["total_current_value_vnd"] == pytest.approx(16_000_000)
    assert d["total_gain_loss_vnd"] == pytest.approx(1_000_000)


def test_update_nonexistent_asset_returns_404(client):
    r = client.put("/api/assets/999999", json={"current_value_vnd": 1_000})
    assert r.status_code == 404
