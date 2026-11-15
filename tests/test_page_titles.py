"""Every full page carries a specific "<Page> — Carange" browser-tab title."""

import re

import pytest

PAGES = [
    "/",
    "/transactions",
    "/import",
    "/pulse",
    "/review",
    "/budget",
    "/savings",
    "/assets",
    "/projects",
    "/income",
    "/forecast",
    "/notes",
    "/settings",
    "/categories",
    "/rules",
    "/templates",
    "/profiles",
]


@pytest.mark.parametrize("path", PAGES)
def test_page_has_specific_title(client, path):
    r = client.get(path)
    assert r.status_code == 200
    m = re.search(r"<title>\s*(.*?)\s*</title>", r.text, re.DOTALL)
    assert m, f"{path} has no <title>"
    title = m.group(1)
    assert title.endswith("— Carange"), f"{path} title {title!r} must end with '— Carange'"
    assert title != "— Carange"
