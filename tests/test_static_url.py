"""static_url() cache-busts /static/ assets so the service worker's cache-first strategy
never serves a stale stylesheet or script after a deploy."""

import os

from app.services import template_globals
from app.services.template_globals import static_url


def test_static_url_appends_content_hash():
    url = static_url("css/carange-tokens.css")
    assert url.startswith("/static/css/carange-tokens.css?v=")
    assert len(url.split("?v=")[1]) == 10


def test_static_url_changes_when_file_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(template_globals, "_STATIC_DIR", tmp_path)
    monkeypatch.setattr(template_globals, "_static_hashes", {})
    f = tmp_path / "app.js"
    f.write_text("one")
    first = static_url("app.js")
    f.write_text("two")
    os.utime(f, (f.stat().st_atime, f.stat().st_mtime + 5))
    assert static_url("app.js") != first


def test_static_url_missing_file_falls_back_to_plain_path():
    assert static_url("does/not/exist.css") == "/static/does/not/exist.css"


def test_base_template_uses_static_url(client):
    html = client.get("/").text
    assert "/static/css/carange-tokens.css?v=" in html
