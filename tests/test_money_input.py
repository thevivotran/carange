"""Money fields use the shared grouped input (data-money) and their labels follow the
display currency instead of a hard-coded "(VND)"."""

import re

import pytest

from app.services import currency_format as cf
from app.services.settings_service import set_setting

PAGES = {
    "/projects": ["new-payment-amount", "rec-amount", "edit-payment-amount"],
    "/assets": ["asset-purchase-price", "asset-current-value"],
    "/savings": ["initial-deposit", "future-amount", "current-amount"],
    "/income": ["income-base-amount", "event-new-base-amount"],
    "/templates": ["amount"],
    "/budget": ["add-amount"],
    "/settings": ["setting-fi-target-vnd", "setting-forecast-buffer"],
}


@pytest.mark.parametrize("path,ids", PAGES.items())
def test_money_fields_are_marked(client, path, ids):
    html = client.get(path).text
    for field_id in ids:
        tag = re.search(rf'<input[^>]*id="{field_id}"[^>]*>', html)
        assert tag, f"{field_id} missing on {path}"
        assert "data-money" in tag.group(0), f"{field_id} on {path} is not a money input"


def test_money_input_script_is_loaded(client):
    assert re.search(r"/static/js/money-input\.js\?v=\w+", client.get("/").text)


@pytest.fixture
def usd(db_session):
    set_setting(db_session, "display_currency", "USD")
    db_session.commit()
    cf._cache["value"] = None
    yield
    cf._cache["value"] = None


@pytest.mark.parametrize("path", ["/projects", "/assets", "/savings", "/templates", "/budget"])
def test_money_labels_follow_display_currency(client, usd, path):
    html = client.get(path).text
    assert "(VND)" not in html, f"{path} still hard-codes (VND)"
