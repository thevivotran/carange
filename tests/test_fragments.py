"""Tests for HTMX fragment endpoints under /fragments/."""

import pytest
from datetime import date
from app.models.database import Transaction, TransactionType, Category


@pytest.fixture()
def category(db_session):
    cat = Category(name="Food", type=TransactionType.EXPENSE, color="#EF4444", icon="utensils")
    db_session.add(cat)
    db_session.commit()
    db_session.refresh(cat)
    return cat


@pytest.fixture()
def sample_transaction(db_session, category):
    tx = Transaction(
        date=date.today(),
        amount=100000,
        type=TransactionType.EXPENSE,
        category_id=category.id,
        description="Lunch",
        source="manual",
    )
    db_session.add(tx)
    db_session.commit()
    db_session.refresh(tx)
    return tx


def test_fragment_list_empty(client):
    r = client.get("/fragments/transactions/list", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "No transactions found" in r.text


def test_fragment_list_with_data(client, sample_transaction):
    r = client.get("/fragments/transactions/list", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "Lunch" in r.text
    assert "₫" in r.text


def test_fragment_list_filter_by_type(client, sample_transaction):
    r = client.get("/fragments/transactions/list?type=income", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "No transactions found" in r.text


def test_fragment_list_trash_mode(client, sample_transaction):
    r = client.get("/fragments/transactions/list?trash=true", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "No transactions found" in r.text  # sample is not deleted


def test_fragment_list_pagination(client):
    r = client.get("/fragments/transactions/list?skip=0&limit=20", headers={"HX-Request": "true"})
    assert r.status_code == 200


def test_fragment_summary(client):
    r = client.get("/fragments/transactions/summary", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "Income" in r.text
    assert "₫" in r.text


def test_fragment_history_no_logs(client, sample_transaction):
    r = client.get(
        f"/fragments/transactions/{sample_transaction.id}/history",
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 200
    assert "No changes recorded" in r.text


def test_fragment_history_nonexistent(client):
    r = client.get("/fragments/transactions/99999/history", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "No changes recorded" in r.text
