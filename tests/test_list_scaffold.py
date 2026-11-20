"""List pages share one header + summary strip, and hide noise while nothing exists yet."""

import re

import pytest

LIST_PAGES = {
    "/assets": "Assets",
    "/projects": "Projects",
    "/savings": "Savings",
    "/income": "Income Sources",
    "/templates": "Templates",
    "/categories": "Categories",
    "/rules": "Rules",
}


@pytest.mark.parametrize("path,title", LIST_PAGES.items())
def test_header_actions_never_wrap(client, path, title):
    html = client.get(path).text
    assert f'<h1 class="text-3xl font-bold text-gray-800">{title}</h1>' in html
    assert "[&>*]:whitespace-nowrap" in html


@pytest.mark.parametrize("name", ["assets", "projects", "income"])
def test_empty_list_has_no_stat_strip_and_marks_itself(client, name):
    fragment = client.get(f"/fragments/{name}/grid").text
    assert "data-stat-strip" not in fragment
    assert "data-list-empty" in fragment
    # the page carries the filter bar the base script hides while data-list-empty is present
    assert "data-list-filters" in client.get(f"/{name}").text


def test_empty_assets_has_a_call_to_action(client):
    assert "Add your first asset" in client.get("/fragments/assets/grid").text


def test_filtered_empty_list_keeps_filters(client):
    html = client.get("/fragments/assets/grid?asset_type=gold").text
    assert "data-list-empty" not in html


def test_stat_strip_renders_when_items_exist(client, db_session):
    from app.models.database import OtherAsset

    db_session.add(
        OtherAsset(
            name="Gold",
            asset_type="gold",
            quantity=1,
            unit="tael",
            purchase_price_vnd=80_000_000,
            current_value_vnd=92_000_000,
        )
    )
    db_session.commit()
    html = client.get("/fragments/assets/grid").text
    assert "data-stat-strip" in html
    assert "grid-cols-2 lg:grid-cols-4" in html
    assert re.search(r'title="[^"]*92,000,000', html), "full value kept in the title"


def test_templates_pagination_starts_hidden(client):
    html = client.get("/templates").text
    assert 'id="templates-pagination" class="hidden' in html
