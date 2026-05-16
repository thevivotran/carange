"""Tests for the import jobs router (upload, list, get, update, delete)."""

import io
import struct
import zlib


def _png_bytes() -> bytes:
    """Minimal valid 1×1 PNG."""
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
    raw = zlib.compress(b"\x00\xff\xff\xff")
    idat = struct.pack(">I", len(raw)) + b"IDAT" + raw + struct.pack(">I", zlib.crc32(b"IDAT" + raw) & 0xFFFFFFFF)
    iend = b"\x00\x00\x00\x00IEND\xaeB`\x82"
    return sig + ihdr + idat + iend


PNG = _png_bytes()
PNG2 = PNG[:-4] + b"\x00\x00\x00\x01"  # slightly different so hash differs


def _upload(client, data=PNG, filename="test.png", content_type="image/png", source_hint=None):
    files = [("files", (filename, io.BytesIO(data), content_type))]
    data_form = {}
    if source_hint:
        data_form["source_hint"] = source_hint
    return client.post("/api/import/jobs", files=files, data=data_form)


# ── Upload ─────────────────────────────────────────────────────────────────────


def test_upload_creates_job(client, tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    import app.routers.import_jobs as ij

    monkeypatch.setattr(ij, "UPLOAD_DIR", str(tmp_path))

    r = _upload(client)
    assert r.status_code == 200
    jobs = r.json()
    assert len(jobs) == 1
    assert jobs[0]["status"] == "pending"
    assert jobs[0]["filename"] == "test.png"


def test_upload_deduplication(client, tmp_path, monkeypatch):
    monkeypatch.setattr("app.routers.import_jobs.UPLOAD_DIR", str(tmp_path))

    r1 = _upload(client)
    r2 = _upload(client)
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()[0]["id"] == r2.json()[0]["id"]


def test_upload_with_source_hint(client, tmp_path, monkeypatch):
    monkeypatch.setattr("app.routers.import_jobs.UPLOAD_DIR", str(tmp_path))

    r = _upload(client, source_hint="timo")
    assert r.status_code == 200
    assert r.json()[0]["source_hint"] == "timo"


def test_upload_invalid_source_hint_ignored(client, tmp_path, monkeypatch):
    monkeypatch.setattr("app.routers.import_jobs.UPLOAD_DIR", str(tmp_path))

    r = _upload(client, source_hint="nonexistent")
    assert r.status_code == 200
    assert r.json()[0]["source_hint"] is None


def test_upload_unsupported_mime_returns_415(client):
    r = _upload(client, content_type="application/pdf")
    assert r.status_code == 415


def test_upload_stores_bare_filename(client, tmp_path, monkeypatch):
    monkeypatch.setattr("app.routers.import_jobs.UPLOAD_DIR", str(tmp_path))

    r = _upload(client)
    file_path = r.json()[0]["file_path"]
    # Must be a bare filename — no directory separators
    assert "/" not in file_path
    assert "\\" not in file_path


# ── List ───────────────────────────────────────────────────────────────────────


def test_list_jobs_empty(client):
    r = client.get("/api/import/jobs")
    assert r.status_code == 200
    assert r.json() == []


def test_list_jobs_returns_all(client, tmp_path, monkeypatch):
    monkeypatch.setattr("app.routers.import_jobs.UPLOAD_DIR", str(tmp_path))

    _upload(client, data=PNG)
    _upload(client, data=PNG2, filename="test2.png")
    r = client.get("/api/import/jobs")
    assert len(r.json()) == 2


def test_list_jobs_filter_by_status(client, tmp_path, monkeypatch):
    monkeypatch.setattr("app.routers.import_jobs.UPLOAD_DIR", str(tmp_path))

    _upload(client)
    r = client.get("/api/import/jobs?status=pending")
    assert r.status_code == 200
    assert all(j["status"] == "pending" for j in r.json())


def test_list_jobs_invalid_status_returns_400(client):
    r = client.get("/api/import/jobs?status=bogus")
    assert r.status_code == 400


# ── Get single ─────────────────────────────────────────────────────────────────


def test_get_job_returns_job(client, tmp_path, monkeypatch):
    monkeypatch.setattr("app.routers.import_jobs.UPLOAD_DIR", str(tmp_path))

    job_id = _upload(client).json()[0]["id"]
    r = client.get(f"/api/import/jobs/{job_id}")
    assert r.status_code == 200
    assert r.json()["id"] == job_id


def test_get_nonexistent_job_returns_404(client):
    r = client.get("/api/import/jobs/9999")
    assert r.status_code == 404


# ── Update ─────────────────────────────────────────────────────────────────────


def test_update_job_status(client, tmp_path, monkeypatch):
    monkeypatch.setattr("app.routers.import_jobs.UPLOAD_DIR", str(tmp_path))

    job_id = _upload(client).json()[0]["id"]
    r = client.patch(f"/api/import/jobs/{job_id}", json={"status": "done", "transaction_count": 3})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "done"
    assert body["transaction_count"] == 3
    assert body["processed_at"] is not None


def test_update_job_sets_processed_at_on_failure(client, tmp_path, monkeypatch):
    monkeypatch.setattr("app.routers.import_jobs.UPLOAD_DIR", str(tmp_path))

    job_id = _upload(client).json()[0]["id"]
    r = client.patch(f"/api/import/jobs/{job_id}", json={"status": "failed", "error_message": "OCR crashed"})
    assert r.status_code == 200
    assert r.json()["processed_at"] is not None


def test_update_nonexistent_job_returns_404(client):
    r = client.patch("/api/import/jobs/9999", json={"status": "done"})
    assert r.status_code == 404


# ── Delete ─────────────────────────────────────────────────────────────────────


def test_delete_job_removes_record(client, tmp_path, monkeypatch):
    monkeypatch.setattr("app.routers.import_jobs.UPLOAD_DIR", str(tmp_path))

    job_id = _upload(client).json()[0]["id"]
    r = client.delete(f"/api/import/jobs/{job_id}")
    assert r.status_code == 200
    assert client.get(f"/api/import/jobs/{job_id}").status_code == 404


def test_delete_job_removes_file(client, tmp_path, monkeypatch):
    monkeypatch.setattr("app.routers.import_jobs.UPLOAD_DIR", str(tmp_path))

    resp = _upload(client)
    job = resp.json()[0]
    file_on_disk = tmp_path / job["file_path"]
    assert file_on_disk.exists()

    client.delete(f"/api/import/jobs/{job['id']}")
    assert not file_on_disk.exists()


def test_delete_nonexistent_job_returns_404(client):
    r = client.delete("/api/import/jobs/9999")
    assert r.status_code == 404
