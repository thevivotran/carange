"""Phone bottom nav: Home · Activity · + · Review (with count) · More; Budget moves to More."""


def _bottom_nav(html: str) -> str:
    start = html.index('id="mobile-bottom-nav"')
    return html[start : html.index("</nav>", start)]


def test_bottom_nav_has_review_with_badge(client):
    nav = _bottom_nav(client.get("/").text)
    assert 'href="/review"' in nav
    assert 'id="mobile-bottom-review-badge"' in nav
    assert ">Activity<" in nav
    assert 'href="/budget"' not in nav


def test_budget_is_reachable_from_more_sheet(client):
    html = client.get("/").text
    sheet = html[html.index('id="mobile-menu"') : html.index('id="mobile-bottom-nav"')]
    assert 'href="/budget"' in sheet
    assert 'href="/review"' not in sheet


def test_review_link_marks_current_page(client):
    nav = _bottom_nav(client.get("/review").text)
    assert 'href="/review" aria-current="page"' in nav
