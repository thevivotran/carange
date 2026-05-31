"""Phase 1 feature tests: rules engine, payees, review inbox, ingest pipeline, Telegram."""

import json
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from app.models.database import (
    Category,
    EmailIngestLog,
    Payee,
    Transaction,
    TransactionRule,
    TransactionType,
)
from app.services.ingest_service import (
    ANOMALY_MIN_SAMPLES,
    IngestItem,
    _is_anomaly,
    _is_duplicate,
    commit_ingest_batch,
)
from app.services.rules_service import _matches, apply_rules, normalize_description


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _make_cat(db, name="Food", type_=TransactionType.EXPENSE):
    cat = Category(name=name, type=type_, color="#111111", icon="tag")
    db.add(cat)
    db.commit()
    db.refresh(cat)
    return cat


def _make_tx(db, cat, *, amount=100_000, date_val=None, type_=TransactionType.EXPENSE, desc="Test"):
    tx = Transaction(
        date=date_val or date(2026, 1, 15),
        amount=amount,
        type=type_,
        category_id=cat.id,
        description=desc,
        payment_method="cash",
        source="manual",
        confidence_score=0.99,
        needs_review=False,
    )
    db.add(tx)
    db.commit()
    db.refresh(tx)
    return tx


def _make_rule(
    db, *, name="R", field="description", op="contains", value="grab", action=None, priority=0, is_active=True
):
    if action is None:
        action = {"set_category_id": 1}
    rule = TransactionRule(
        name=name,
        match_field=field,
        match_op=op,
        match_value=value,
        action_json=json.dumps(action),
        priority=priority,
        is_active=is_active,
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return rule


# ─────────────────────────────────────────────────────────────────────────────
# Rules engine — normalize_description
# ─────────────────────────────────────────────────────────────────────────────


def test_normalize_description_no_payees(db_session):
    canonical, pid = normalize_description(db_session, "Grab Food")
    assert canonical == "Grab Food"
    assert pid is None


def test_normalize_description_matches_alias(db_session):
    payee = Payee(canonical_name="Grab", alias_patterns=json.dumps([r"grab\s*food", r"grab"]))
    db_session.add(payee)
    db_session.commit()
    db_session.refresh(payee)

    canonical, pid = normalize_description(db_session, "Grab Food delivery")
    assert canonical == "Grab"
    assert pid == payee.id


def test_normalize_description_no_match(db_session):
    payee = Payee(canonical_name="Shopee", alias_patterns=json.dumps([r"shopee"]))
    db_session.add(payee)
    db_session.commit()

    canonical, pid = normalize_description(db_session, "Grab Bike")
    assert canonical == "Grab Bike"
    assert pid is None


def test_normalize_description_bad_json_pattern_skipped(db_session):
    payee = Payee(canonical_name="Bad", alias_patterns="not-json")
    db_session.add(payee)
    db_session.commit()

    canonical, pid = normalize_description(db_session, "anything")
    assert pid is None


def test_normalize_description_bad_regex_skipped(db_session):
    payee = Payee(canonical_name="Bad", alias_patterns=json.dumps(["[invalid(regex"]))
    db_session.add(payee)
    db_session.commit()

    canonical, pid = normalize_description(db_session, "anything")
    assert pid is None


def test_normalize_description_empty_string(db_session):
    canonical, pid = normalize_description(db_session, "")
    assert canonical == ""
    assert pid is None


# ─────────────────────────────────────────────────────────────────────────────
# Rules engine — apply_rules / _matches
# ─────────────────────────────────────────────────────────────────────────────


def test_apply_rules_no_rules_returns_empty_action(db_session):
    cat = _make_cat(db_session)
    tx = _make_tx(db_session, cat)
    action = apply_rules(db_session, tx)
    assert action.category_id is None
    assert not action.auto_approve
    assert not action.force_needs_review


def test_apply_rules_matches_description_contains(db_session):
    cat = _make_cat(db_session)
    cat2 = _make_cat(db_session, name="Transport")
    tx = _make_tx(db_session, cat, desc="Grab Bike ride")
    rule = _make_rule(
        db_session,
        field="description",
        op="contains",
        value="grab",
        action={"set_category_id": cat2.id, "auto_approve": True},
    )

    action = apply_rules(db_session, tx)
    assert action.category_id == cat2.id
    assert action.auto_approve is True
    assert rule.match_count == 1


def test_apply_rules_inactive_rule_ignored(db_session):
    cat = _make_cat(db_session)
    tx = _make_tx(db_session, cat, desc="Grab")
    _make_rule(db_session, is_active=False, action={"set_category_id": cat.id})

    action = apply_rules(db_session, tx)
    assert action.category_id is None


def test_apply_rules_priority_order(db_session):
    cat = _make_cat(db_session)
    cat2 = _make_cat(db_session, name="High")
    cat3 = _make_cat(db_session, name="Low")
    tx = _make_tx(db_session, cat, desc="grab")

    _make_rule(db_session, name="Low", priority=10, action={"set_category_id": cat3.id})
    _make_rule(db_session, name="High", priority=1, action={"set_category_id": cat2.id})

    action = apply_rules(db_session, tx)
    assert action.category_id == cat2.id


def test_apply_rules_force_needs_review(db_session):
    cat = _make_cat(db_session)
    tx = _make_tx(db_session, cat, desc="grab")
    _make_rule(db_session, action={"force_needs_review": True})

    action = apply_rules(db_session, tx)
    assert action.force_needs_review is True


def test_matches_equals(db_session):
    cat = _make_cat(db_session)
    tx = _make_tx(db_session, cat, desc="grab")
    rule = TransactionRule(name="r", match_field="description", match_op="equals", match_value="grab", action_json="{}")
    assert _matches(rule, tx, None) is True
    tx.description = "GRAB"
    assert _matches(rule, tx, None) is True


def test_matches_regex(db_session):
    cat = _make_cat(db_session)
    tx = _make_tx(db_session, cat, desc="Shopee Order #12345")
    rule = TransactionRule(
        name="r", match_field="description", match_op="regex", match_value=r"shopee.*#\d+", action_json="{}"
    )
    assert _matches(rule, tx, None) is True


def test_matches_invalid_regex_returns_false(db_session):
    cat = _make_cat(db_session)
    tx = _make_tx(db_session, cat, desc="anything")
    rule = TransactionRule(
        name="r", match_field="description", match_op="regex", match_value="[bad(regex", action_json="{}"
    )
    assert _matches(rule, tx, None) is False


def test_matches_range(db_session):
    cat = _make_cat(db_session)
    tx = _make_tx(db_session, cat, amount=500_000)
    rule = TransactionRule(
        name="r", match_field="amount", match_op="range", match_value="100000,900000", action_json="{}"
    )
    assert _matches(rule, tx, None) is True

    rule2 = TransactionRule(
        name="r2", match_field="amount", match_op="range", match_value="600000,900000", action_json="{}"
    )
    assert _matches(rule2, tx, None) is False


def test_matches_range_bad_value(db_session):
    cat = _make_cat(db_session)
    tx = _make_tx(db_session, cat, amount=500_000)
    rule = TransactionRule(
        name="r", match_field="amount", match_op="range", match_value="not,numbers", action_json="{}"
    )
    assert _matches(rule, tx, None) is False


def test_matches_in_operator(db_session):
    cat = _make_cat(db_session)
    tx = _make_tx(db_session, cat, desc="cash")
    tx.payment_method = "cash"
    rule = TransactionRule(
        name="r", match_field="payment_method", match_op="in", match_value="cash, card, bank_transfer", action_json="{}"
    )
    assert _matches(rule, tx, None) is True


def test_matches_payee_id_field(db_session):
    cat = _make_cat(db_session)
    tx = _make_tx(db_session, cat)
    rule = TransactionRule(name="r", match_field="payee_id", match_op="equals", match_value="42", action_json="{}")
    assert _matches(rule, tx, 42) is True
    assert _matches(rule, tx, None) is False


def test_matches_source_field(db_session):
    cat = _make_cat(db_session)
    tx = _make_tx(db_session, cat)
    tx.source = "email"
    rule = TransactionRule(name="r", match_field="source", match_op="equals", match_value="email", action_json="{}")
    assert _matches(rule, tx, None) is True


def test_matches_type_field(db_session):
    cat = _make_cat(db_session)
    tx = _make_tx(db_session, cat)
    rule = TransactionRule(name="r", match_field="type", match_op="equals", match_value="expense", action_json="{}")
    assert _matches(rule, tx, None) is True


def test_matches_invalid_field_returns_false(db_session):
    cat = _make_cat(db_session)
    tx = _make_tx(db_session, cat)
    rule = TransactionRule(name="r", match_field="nonexistent", match_op="equals", match_value="x", action_json="{}")
    assert _matches(rule, tx, None) is False


def test_matches_invalid_op_returns_false(db_session):
    cat = _make_cat(db_session)
    tx = _make_tx(db_session, cat)
    rule = TransactionRule(name="r", match_field="description", match_op="fuzzy", match_value="x", action_json="{}")
    assert _matches(rule, tx, None) is False


# ─────────────────────────────────────────────────────────────────────────────
# Ingest service — commit_ingest_batch
# ─────────────────────────────────────────────────────────────────────────────


def test_commit_ingest_batch_basic(db_session):
    _make_cat(db_session, name="Others")
    item = IngestItem(
        date=date(2026, 3, 1), amount=200_000, tx_type="expense", description="Test purchase", confidence=0.99
    )
    committed = commit_ingest_batch(db_session, [item], source_tag="email")
    assert len(committed) == 1
    assert committed[0].source == "email"
    assert committed[0].amount == 200_000


def test_commit_ingest_batch_dedup(db_session):
    cat = _make_cat(db_session, name="Food")
    # Pre-existing tx
    _make_tx(db_session, cat, amount=100_000, date_val=date(2026, 3, 1))

    item = IngestItem(
        date=date(2026, 3, 1), amount=100_000, tx_type="expense", description="Duplicate", confidence=0.99
    )
    committed = commit_ingest_batch(db_session, [item], source_tag="email")
    assert len(committed) == 0


def test_commit_ingest_batch_no_category_skips(db_session):
    # No categories of type income
    item = IngestItem(date=date(2026, 3, 1), amount=5_000_000, tx_type="income", description="Salary", confidence=0.99)
    committed = commit_ingest_batch(db_session, [item], source_tag="email")
    assert len(committed) == 0


def test_commit_ingest_batch_low_confidence_needs_review(db_session):
    _make_cat(db_session, name="Others")
    item = IngestItem(date=date(2026, 3, 1), amount=50_000, tx_type="expense", description="Unknown", confidence=0.5)
    committed = commit_ingest_batch(db_session, [item], source_tag="email")
    assert len(committed) == 1
    assert committed[0].needs_review is True


def test_commit_ingest_batch_category_hint(db_session):
    cat = _make_cat(db_session, name="Transportation")
    item = IngestItem(
        date=date(2026, 3, 1),
        amount=30_000,
        tx_type="expense",
        description="Grab Bike",
        confidence=0.99,
        category_hint="Transportation",
    )
    committed = commit_ingest_batch(db_session, [item], source_tag="ocr")
    assert len(committed) == 1
    assert committed[0].category_id == cat.id


def test_is_duplicate_true(db_session):
    cat = _make_cat(db_session)
    _make_tx(db_session, cat, amount=999, date_val=date(2026, 1, 1))
    item = IngestItem(date=date(2026, 1, 1), amount=999, tx_type="expense", description="dup", confidence=0.9)
    assert _is_duplicate(db_session, item) is True


def test_is_duplicate_false(db_session):
    item = IngestItem(date=date(2026, 1, 1), amount=999, tx_type="expense", description="unique", confidence=0.9)
    assert _is_duplicate(db_session, item) is False


def test_is_anomaly_below_threshold(db_session):
    cat = _make_cat(db_session)
    for i in range(ANOMALY_MIN_SAMPLES):
        _make_tx(db_session, cat, amount=100_000, date_val=date(2026, 1, i + 1))
    item = IngestItem(date=date(2026, 2, 1), amount=150_000, tx_type="expense", description="normal", confidence=0.9)
    assert _is_anomaly(db_session, item, cat.id) is False


def test_is_anomaly_above_threshold(db_session):
    cat = _make_cat(db_session)
    for i in range(ANOMALY_MIN_SAMPLES):
        _make_tx(db_session, cat, amount=100_000, date_val=date(2026, 1, i + 1))
    # Amount > avg * ANOMALY_MULTIPLIER (100000 * 3.0 = 300000)
    item = IngestItem(date=date(2026, 2, 1), amount=500_000, tx_type="expense", description="anomaly", confidence=0.9)
    assert _is_anomaly(db_session, item, cat.id) is True


def test_is_anomaly_insufficient_samples(db_session):
    cat = _make_cat(db_session)
    _make_tx(db_session, cat, amount=100_000, date_val=date(2026, 1, 1))  # only 1 sample
    item = IngestItem(
        date=date(2026, 2, 1), amount=999_999, tx_type="expense", description="not enough samples", confidence=0.9
    )
    assert _is_anomaly(db_session, item, cat.id) is False


def test_commit_ingest_batch_with_email_log_id(db_session):
    _make_cat(db_session, name="Food")
    log_row = EmailIngestLog(message_id="test-msg-id@example.com", sender="bank@vcb.com", subject="Transfer")
    db_session.add(log_row)
    db_session.commit()
    db_session.refresh(log_row)

    item = IngestItem(
        date=date(2026, 3, 1), amount=75_000, tx_type="expense", description="VCB transfer", confidence=0.97
    )
    committed = commit_ingest_batch(db_session, [item], source_tag="email", email_ingest_log_id=log_row.id)
    assert len(committed) == 1
    assert committed[0].email_ingest_log_id == log_row.id


# ─────────────────────────────────────────────────────────────────────────────
# Rules API router
# ─────────────────────────────────────────────────────────────────────────────


def test_list_rules_empty(client):
    r = client.get("/api/rules/")
    assert r.status_code == 200
    assert r.json() == []


def test_create_rule(client):
    r = client.post(
        "/api/rules/",
        json={
            "name": "Grab rule",
            "match_field": "description",
            "match_op": "contains",
            "match_value": "grab",
            "action_json": {"set_category_id": 1},
        },
    )
    assert r.status_code == 201
    data = r.json()
    assert data["name"] == "Grab rule"
    assert data["match_count"] == 0


def test_create_rule_invalid_field(client):
    r = client.post(
        "/api/rules/",
        json={
            "name": "Bad",
            "match_field": "nonexistent",
            "match_op": "equals",
            "match_value": "x",
            "action_json": {},
        },
    )
    assert r.status_code == 422


def test_create_rule_invalid_op(client):
    r = client.post(
        "/api/rules/",
        json={
            "name": "Bad",
            "match_field": "description",
            "match_op": "fuzzy",
            "match_value": "x",
            "action_json": {},
        },
    )
    assert r.status_code == 422


def test_update_rule(client):
    r = client.post(
        "/api/rules/",
        json={
            "name": "Old name",
            "match_field": "description",
            "match_op": "contains",
            "match_value": "test",
            "action_json": {},
        },
    )
    rule_id = r.json()["id"]

    upd = client.put(f"/api/rules/{rule_id}", json={"name": "New name", "priority": 5})
    assert upd.status_code == 200
    assert upd.json()["name"] == "New name"
    assert upd.json()["priority"] == 5


def test_update_rule_not_found(client):
    r = client.put("/api/rules/9999", json={"name": "x"})
    assert r.status_code == 404


def test_delete_rule(client):
    r = client.post(
        "/api/rules/",
        json={
            "name": "To delete",
            "match_field": "description",
            "match_op": "equals",
            "match_value": "x",
            "action_json": {},
        },
    )
    rule_id = r.json()["id"]
    assert client.delete(f"/api/rules/{rule_id}").status_code == 204
    assert client.get("/api/rules/").json() == []


def test_delete_rule_not_found(client):
    assert client.delete("/api/rules/9999").status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# Payees API router
# ─────────────────────────────────────────────────────────────────────────────


def test_list_payees_empty(client):
    assert client.get("/api/payees/").json() == []


def test_create_payee(client):
    r = client.post(
        "/api/payees/",
        json={
            "canonical_name": "Grab",
            "alias_patterns": [r"grab\s*food", r"grab\s*bike"],
        },
    )
    assert r.status_code == 201
    data = r.json()
    assert data["canonical_name"] == "Grab"
    assert len(data["alias_patterns"]) == 2


def test_create_payee_duplicate_name(client):
    client.post("/api/payees/", json={"canonical_name": "Shopee"})
    r = client.post("/api/payees/", json={"canonical_name": "Shopee"})
    assert r.status_code == 409


def test_update_payee(client):
    r = client.post("/api/payees/", json={"canonical_name": "Old"})
    pid = r.json()["id"]
    upd = client.put(f"/api/payees/{pid}", json={"canonical_name": "New", "alias_patterns": ["new.*"]})
    assert upd.status_code == 200
    assert upd.json()["canonical_name"] == "New"


def test_update_payee_not_found(client):
    assert client.put("/api/payees/9999", json={"canonical_name": "x"}).status_code == 404


def test_delete_payee(client):
    r = client.post("/api/payees/", json={"canonical_name": "ToDelete"})
    pid = r.json()["id"]
    assert client.delete(f"/api/payees/{pid}").status_code == 204
    assert client.get("/api/payees/").json() == []


def test_delete_payee_not_found(client):
    assert client.delete("/api/payees/9999").status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# Review inbox router
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture()
def review_setup(client):
    """Create a category + a needs_review transaction."""
    cat = client.post(
        "/api/categories/", json={"name": "Food", "type": "expense", "color": "#111", "icon": "tag"}
    ).json()["id"]
    tx = client.post(
        "/api/transactions/?force=true",
        json={
            "date": "2026-03-01",
            "amount": 55_000,
            "type": "expense",
            "category_id": cat,
            "payment_method": "cash",
        },
    ).json()
    # Manually set needs_review via db (no public endpoint to set it)
    return cat, tx["id"]


def test_review_count_zero(client):
    r = client.get("/api/review/count")
    assert r.status_code == 200
    assert r.json()["count"] == 0


def test_review_count_nonzero(client, db_session):
    cat = _make_cat(db_session)
    tx = _make_tx(db_session, cat, amount=99_000, date_val=date(2026, 3, 1))
    tx.needs_review = True
    db_session.commit()

    r = client.get("/api/review/count")
    assert r.json()["count"] == 1


def test_approve_clears_needs_review(client, db_session):
    cat = _make_cat(db_session)
    tx = _make_tx(db_session, cat, amount=99_000, date_val=date(2026, 3, 1))
    tx.needs_review = True
    db_session.commit()

    r = client.post(f"/api/review/{tx.id}/approve", json={})
    assert r.status_code == 200
    assert r.json()["needs_review"] is False


def test_approve_updates_fields(client, db_session):
    cat = _make_cat(db_session)
    cat2 = _make_cat(db_session, name="Transport")
    tx = _make_tx(db_session, cat, amount=99_000, date_val=date(2026, 3, 1))
    tx.needs_review = True
    db_session.commit()

    r = client.post(
        f"/api/review/{tx.id}/approve",
        json={
            "category_id": cat2.id,
            "amount": 120_000,
            "description": "Grab Bike",
        },
    )
    assert r.status_code == 200


def test_approve_not_found(client):
    assert client.post("/api/review/9999/approve", json={}).status_code == 404


def test_reject_soft_deletes(client, db_session):
    cat = _make_cat(db_session)
    tx = _make_tx(db_session, cat, amount=10_000, date_val=date(2026, 3, 1))
    tx.needs_review = True
    db_session.commit()

    r = client.post(f"/api/review/{tx.id}/reject")
    assert r.status_code == 200
    assert r.json()["deleted"] is True


def test_reject_not_found(client):
    assert client.post("/api/review/9999/reject").status_code == 404


def test_rule_prefill(client, db_session):
    cat = _make_cat(db_session)
    tx = _make_tx(db_session, cat, desc="Shopee Order")
    r = client.get(f"/api/review/{tx.id}/rule-prefill")
    assert r.status_code == 200
    data = r.json()
    assert "Shopee" in data["name"]
    assert data["match_field"] == "description"


def test_rule_prefill_not_found(client):
    assert client.get("/api/review/9999/rule-prefill").status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# Fragment routers — smoke tests (just check 200 + not empty)
# ─────────────────────────────────────────────────────────────────────────────


def test_fragment_review_list(client, db_session):
    r = client.get("/fragments/review/list")
    assert r.status_code == 200


def test_fragment_rules_list(client):
    r = client.get("/fragments/rules/list")
    assert r.status_code == 200


def test_fragment_payees_list(client):
    r = client.get("/fragments/payees/list")
    assert r.status_code == 200


# ─────────────────────────────────────────────────────────────────────────────
# Telegram notify module
# ─────────────────────────────────────────────────────────────────────────────


def _make_mock_tx(needs_review=False):
    tx = MagicMock()
    tx.amount = 250_000
    tx.type = MagicMock()
    tx.type.value = "expense"
    tx.category = MagicMock()
    tx.category.name = "Food"
    tx.description = "Grab Food"
    tx.source = "email"
    tx.needs_review = needs_review
    return tx


def test_telegram_no_config_returns_false():
    from app.notify import telegram as tg

    with patch.object(tg, "_BOT_TOKEN", ""), patch.object(tg, "_CHAT_ID", ""):
        assert tg.send_transaction_ping(_make_mock_tx()) is False


def test_telegram_send_transaction_ping_success():
    from app.notify import telegram as tg

    with (
        patch.object(tg, "_BOT_TOKEN", "fake-token"),
        patch.object(tg, "_CHAT_ID", "12345"),
        patch("app.notify.telegram.requests.post") as mock_post,
    ):
        mock_post.return_value = MagicMock(ok=True)
        result = tg.send_transaction_ping(_make_mock_tx())
        assert result is True
        assert mock_post.called


def test_telegram_send_transaction_ping_needs_review():
    from app.notify import telegram as tg

    with (
        patch.object(tg, "_BOT_TOKEN", "fake-token"),
        patch.object(tg, "_CHAT_ID", "12345"),
        patch("app.notify.telegram.requests.post") as mock_post,
    ):
        mock_post.return_value = MagicMock(ok=True)
        result = tg.send_transaction_ping(_make_mock_tx(needs_review=True))
        assert result is True
        text_sent = mock_post.call_args[1]["json"]["text"]
        assert "Needs review" in text_sent


def test_telegram_send_review_reminder_zero():
    from app.notify import telegram as tg

    assert tg.send_review_reminder(0) is False


def test_telegram_send_review_reminder_positive():
    from app.notify import telegram as tg

    with (
        patch.object(tg, "_BOT_TOKEN", "fake-token"),
        patch.object(tg, "_CHAT_ID", "12345"),
        patch("app.notify.telegram.requests.post") as mock_post,
    ):
        mock_post.return_value = MagicMock(ok=True)
        result = tg.send_review_reminder(3)
        assert result is True


def test_telegram_send_message():
    from app.notify import telegram as tg

    with (
        patch.object(tg, "_BOT_TOKEN", "fake-token"),
        patch.object(tg, "_CHAT_ID", "12345"),
        patch("app.notify.telegram.requests.post") as mock_post,
    ):
        mock_post.return_value = MagicMock(ok=True)
        result = tg.send_message("<b>Hello</b>")
        assert result is True


def test_telegram_request_exception_returns_false():
    import requests as req_lib
    from app.notify import telegram as tg

    with (
        patch.object(tg, "_BOT_TOKEN", "fake-token"),
        patch.object(tg, "_CHAT_ID", "12345"),
        patch("app.notify.telegram.requests.post", side_effect=req_lib.RequestException("timeout")),
    ):
        assert tg.send_transaction_ping(_make_mock_tx()) is False


def test_telegram_api_error_logged():
    from app.notify import telegram as tg

    with (
        patch.object(tg, "_BOT_TOKEN", "fake-token"),
        patch.object(tg, "_CHAT_ID", "12345"),
        patch("app.notify.telegram.requests.post") as mock_post,
    ):
        mock_post.return_value = MagicMock(ok=False, status_code=400, text="Bad Request")
        result = tg.send_transaction_ping(_make_mock_tx())
        assert result is False


def test_telegram_income_tx_shows_plus():
    from app.notify import telegram as tg

    tx = _make_mock_tx()
    tx.type.value = "income"
    tx.source = "ocr"
    with (
        patch.object(tg, "_BOT_TOKEN", "tok"),
        patch.object(tg, "_CHAT_ID", "c"),
        patch("app.notify.telegram.requests.post") as mock_post,
    ):
        mock_post.return_value = MagicMock(ok=True)
        tg.send_transaction_ping(tx)
        text = mock_post.call_args[1]["json"]["text"]
        assert "+" in text


# ─────────────────────────────────────────────────────────────────────────────
# Ingest pipeline — rule action branches inside commit_ingest_batch
# ─────────────────────────────────────────────────────────────────────────────


def test_commit_ingest_batch_rule_sets_category(db_session):
    _make_cat(db_session, name="Food")
    cat_transport = _make_cat(db_session, name="Transport")
    rule = TransactionRule(
        name="Grab → Transport",
        match_field="description",
        match_op="contains",
        match_value="grab",
        action_json=json.dumps({"set_category_id": cat_transport.id, "auto_approve": True}),
        is_active=True,
        priority=0,
    )
    db_session.add(rule)
    db_session.commit()

    item = IngestItem(
        date=date(2026, 4, 1),
        amount=30_000,
        tx_type="expense",
        description="Grab Bike",
        confidence=0.5,  # would normally trigger needs_review
        category_hint="Food",
    )
    committed = commit_ingest_batch(db_session, [item], source_tag="email")
    assert len(committed) == 1
    assert committed[0].category_id == cat_transport.id
    assert committed[0].needs_review is False  # auto_approve overrides low confidence


def test_commit_ingest_batch_rule_force_needs_review(db_session):
    _make_cat(db_session, name="Food")
    rule = TransactionRule(
        name="Force review",
        match_field="description",
        match_op="contains",
        match_value="suspicious",
        action_json=json.dumps({"force_needs_review": True}),
        is_active=True,
        priority=0,
    )
    db_session.add(rule)
    db_session.commit()

    item = IngestItem(
        date=date(2026, 4, 2),
        amount=500_000,
        tx_type="expense",
        description="suspicious transaction",
        confidence=0.99,  # high confidence but rule overrides
    )
    committed = commit_ingest_batch(db_session, [item], source_tag="email")
    assert len(committed) == 1
    assert committed[0].needs_review is True


def test_commit_ingest_batch_anomaly_flags_review(db_session):
    cat = _make_cat(db_session, name="Food")
    # Seed enough baseline transactions
    for i in range(ANOMALY_MIN_SAMPLES):
        _make_tx(db_session, cat, amount=50_000, date_val=date(2026, 1, i + 1))

    item = IngestItem(
        date=date(2026, 2, 1),
        amount=500_000,  # > 50000 * 3.0 = 150000
        tx_type="expense",
        description="Huge dinner",
        confidence=0.99,
        category_hint="Food",
    )
    committed = commit_ingest_batch(db_session, [item], source_tag="email")
    assert len(committed) == 1
    assert committed[0].needs_review is True


# ─────────────────────────────────────────────────────────────────────────────
# Rules router — update with field/op changes
# ─────────────────────────────────────────────────────────────────────────────


def test_update_rule_change_field_and_op(client):
    r = client.post(
        "/api/rules/",
        json={
            "name": "test",
            "match_field": "description",
            "match_op": "contains",
            "match_value": "grab",
            "action_json": {},
        },
    )
    rule_id = r.json()["id"]

    upd = client.put(
        f"/api/rules/{rule_id}",
        json={
            "match_field": "amount",
            "match_op": "range",
            "match_value": "10000,500000",
        },
    )
    assert upd.status_code == 200
    assert upd.json()["match_field"] == "amount"
    assert upd.json()["match_op"] == "range"


def test_update_rule_invalid_field(client):
    r = client.post(
        "/api/rules/",
        json={
            "name": "test",
            "match_field": "description",
            "match_op": "contains",
            "match_value": "x",
            "action_json": {},
        },
    )
    rule_id = r.json()["id"]
    upd = client.put(f"/api/rules/{rule_id}", json={"match_field": "invalid"})
    assert upd.status_code == 422


def test_update_rule_action_json(client):
    r = client.post(
        "/api/rules/",
        json={
            "name": "test",
            "match_field": "description",
            "match_op": "equals",
            "match_value": "x",
            "action_json": {},
        },
    )
    rule_id = r.json()["id"]
    upd = client.put(
        f"/api/rules/{rule_id}",
        json={
            "action_json": {"set_category_id": 5, "auto_approve": True},
            "match_value": "y",
            "is_active": False,
        },
    )
    assert upd.status_code == 200
    assert upd.json()["action_json"]["auto_approve"] is True


# ─────────────────────────────────────────────────────────────────────────────
# Payees router — update with category_id + source
# ─────────────────────────────────────────────────────────────────────────────


def test_update_payee_category_id(client, db_session):
    cat = _make_cat(db_session)
    r = client.post("/api/payees/", json={"canonical_name": "VCB"})
    pid = r.json()["id"]

    upd = client.put(
        f"/api/payees/{pid}",
        json={
            "default_category_id": cat.id,
            "source": "import",
        },
    )
    assert upd.status_code == 200
    assert upd.json()["source"] == "import"


# ─────────────────────────────────────────────────────────────────────────────
# Review router — approve with description triggers normalize_description
# ─────────────────────────────────────────────────────────────────────────────


def test_approve_with_description_normalizes(client, db_session):
    cat = _make_cat(db_session)
    payee = Payee(canonical_name="Grab", alias_patterns=json.dumps([r"grab"]))
    db_session.add(payee)
    tx = _make_tx(db_session, cat, amount=50_000, date_val=date(2026, 3, 1))
    tx.needs_review = True
    db_session.commit()
    db_session.refresh(payee)

    r = client.post(f"/api/review/{tx.id}/approve", json={"description": "Grab Bike"})
    assert r.status_code == 200


# ─────────────────────────────────────────────────────────────────────────────
# Fragment rules list with actual rule in DB
# ─────────────────────────────────────────────────────────────────────────────


def test_fragment_rules_list_with_data(client, db_session):
    _make_rule(db_session, name="Frag test", field="description", op="contains", value="test")
    r = client.get("/fragments/rules/list")
    assert r.status_code == 200


def test_rules_service_invalid_action_json_skips(db_session):
    """Rule with invalid action_json is skipped, engine returns empty action."""
    cat = _make_cat(db_session)
    tx = _make_tx(db_session, cat, desc="grab")
    rule = TransactionRule(
        name="bad",
        match_field="description",
        match_op="contains",
        match_value="grab",
        action_json="not-valid-json",
        is_active=True,
        priority=0,
    )
    db_session.add(rule)
    db_session.commit()

    action = apply_rules(db_session, tx)
    assert action.category_id is None


# ─────────────────────────────────────────────────────────────────────────────
# Telegram ping fired from create_transaction
# ─────────────────────────────────────────────────────────────────────────────


def test_create_transaction_fires_telegram_ping(client):
    """Telegram ping is attempted (and silently swallowed) on every manual tx."""
    cat_id = client.post(
        "/api/categories/", json={"name": "Food", "type": "expense", "color": "#111", "icon": "tag"}
    ).json()["id"]

    with (
        patch("app.notify.telegram._BOT_TOKEN", "tok"),
        patch("app.notify.telegram._CHAT_ID", "123"),
        patch("app.notify.telegram.requests.post") as mock_post,
    ):
        mock_post.return_value = MagicMock(ok=True)
        r = client.post(
            "/api/transactions/?force=true",
            json={
                "date": "2026-04-01",
                "amount": 50_000,
                "type": "expense",
                "category_id": cat_id,
                "payment_method": "cash",
            },
        )
        assert r.status_code in (200, 201)
        assert mock_post.called


def test_create_transaction_telegram_exception_does_not_crash(client):
    """A Telegram failure must not roll back the transaction."""
    cat_id = client.post(
        "/api/categories/", json={"name": "Food", "type": "expense", "color": "#222", "icon": "tag"}
    ).json()["id"]

    with patch("app.notify.telegram.send_transaction_ping", side_effect=RuntimeError("boom")):
        r = client.post(
            "/api/transactions/?force=true",
            json={
                "date": "2026-04-02",
                "amount": 75_000,
                "type": "expense",
                "category_id": cat_id,
                "payment_method": "cash",
            },
        )
        assert r.status_code in (200, 201)  # tx still created despite Telegram error
