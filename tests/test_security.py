"""Regression tests for security hardening: CSRF origin guard, security headers,
reflected XSS, open redirects, and upload limits."""

import io

import pytest

from app.models.database import User
from app.services.profiles import safe_next_path


def _create_user(db_session, name="Vi"):
    user = User(name=name, color="#2563EB")
    db_session.add(user)
    db_session.commit()
    return user


# ── Same-origin (CSRF) guard ─────────────────────────────────────────────────


def test_cross_origin_post_is_blocked(profile_client, db_session):
    _create_user(db_session, name="Vi")
    victim = _create_user(db_session, name="Wife")
    r = profile_client.post(
        f"/profiles/{victim.id}/delete",
        headers={"origin": "https://evil.example.com"},
        follow_redirects=False,
    )
    assert r.status_code == 403
    assert db_session.query(User).filter(User.id == victim.id).first() is not None


def test_cross_origin_referer_without_origin_is_blocked(client):
    r = client.post(
        "/settings/pay-cycle",
        data={"month_start_day": "5"},
        headers={"referer": "https://evil.example.com/page"},
    )
    assert r.status_code == 403


def test_null_origin_is_blocked(client):
    r = client.post("/settings/pay-cycle", data={"month_start_day": "5"}, headers={"origin": "null"})
    assert r.status_code == 403


def test_same_origin_post_is_allowed(client):
    r = client.post("/settings/pay-cycle", data={"month_start_day": "5"}, headers={"origin": "http://testserver"})
    assert r.status_code == 200


def test_forwarded_host_origin_is_allowed(client):
    r = client.post(
        "/settings/pay-cycle",
        data={"month_start_day": "5"},
        headers={"origin": "https://carange.example.ts.net", "x-forwarded-host": "carange.example.ts.net"},
    )
    assert r.status_code == 200


def test_cross_origin_get_is_not_blocked(client):
    r = client.get("/health", headers={"origin": "https://evil.example.com"})
    assert r.status_code == 200


def test_security_headers_present(client):
    r = client.get("/health")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["referrer-policy"] == "same-origin"


# ── Reflected XSS ────────────────────────────────────────────────────────────


def test_pay_cycle_error_escapes_user_input(client):
    r = client.post("/settings/pay-cycle", data={"month_start_day": "<img src=x onerror=alert(1)>"})
    assert r.status_code == 400
    assert "<img" not in r.text
    assert "&lt;img" in r.text


# ── Open redirect ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("raw", ["/\\evil.example.com", "/\t/evil.example.com", "/\n/evil.example.com", "//x"])
def test_safe_next_path_rejects_browser_normalised_redirects(raw):
    assert safe_next_path(raw) == "/"


def test_safe_next_path_keeps_local_paths():
    assert safe_next_path("/budget?month=5") == "/budget?month=5"


# ── Upload limits ────────────────────────────────────────────────────────────


def test_upload_ignores_dangerous_extension(client, tmp_path, monkeypatch):
    monkeypatch.setattr("app.routers.import_jobs.UPLOAD_DIR", str(tmp_path))
    files = [("files", ("shot.html", io.BytesIO(b"\x89PNG fake"), "image/png"))]
    r = client.post("/api/import/jobs", files=files)
    assert r.status_code == 200
    assert r.json()[0]["file_path"].endswith(".jpg")


def test_upload_rejects_oversized_file(client, tmp_path, monkeypatch):
    monkeypatch.setattr("app.routers.import_jobs.UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr("app.routers.import_jobs.MAX_FILE_SIZE", 10)
    files = [("files", ("big.png", io.BytesIO(b"x" * 11), "image/png"))]
    r = client.post("/api/import/jobs", files=files)
    assert r.status_code == 413


def test_bulk_csv_rejects_oversized_file(client, monkeypatch):
    monkeypatch.setattr("app.routers.transactions.MAX_CSV_SIZE", 10)
    files = {"file": ("big.csv", io.BytesIO(b"a,b\n" * 10), "text/csv")}
    r = client.post("/api/transactions/bulk-upload", files=files)
    assert r.status_code == 413
