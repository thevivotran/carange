"""Every chart canvas is exposed to assistive tech (role="img" + a name), and the shared
describeChart() helper that adds the summary + data table is available on every page."""

import re


def _canvases(html: str) -> list[str]:
    return re.findall(r"<canvas\b[^>]*>", html)


def test_dashboard_canvases_have_role_and_label(client):
    html = client.get("/").text
    canvases = _canvases(html)
    assert canvases, "full layout should render chart canvases"
    for tag in canvases:
        assert 'role="img"' in tag, tag
        assert re.search(r'aria-label="[^"]+"', tag), tag


def test_forecast_canvas_has_role_and_label(client):
    tag = _canvases(client.get("/forecast").text)[0]
    assert 'role="img"' in tag and "aria-label=" in tag


def test_describe_chart_helper_is_global(client):
    assert "window.describeChart = function" in client.get("/forecast").text


def test_cash_flow_chart_init_is_guarded(client):
    html = client.get("/").text
    assert "if (document.getElementById('cashFlowChart'))" in html
