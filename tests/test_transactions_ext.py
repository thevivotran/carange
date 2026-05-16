"""Extended transaction tests — covers filters, stat endpoints, and bulk upload."""

import io
import pytest


@pytest.fixture()
def cat_ids(client):
    inc = client.post(
        "/api/categories/", json={"name": "Salary", "type": "income", "color": "#10B981", "icon": "money"}
    ).json()["id"]
    exp = client.post(
        "/api/categories/", json={"name": "Food", "type": "expense", "color": "#EF4444", "icon": "utensils"}
    ).json()["id"]
    return {"income": inc, "expense": exp}


def _make_tx(client, *, date_str, amount, type_, category_id, description="", **extra):
    payload = {
        "date": date_str,
        "amount": amount,
        "type": type_,
        "category_id": category_id,
        "description": description,
        "payment_method": "cash",
        "is_savings_related": False,
    }
    payload.update(extra)
    return client.post("/api/transactions/", json=payload)


# ── Create validation ─────────────────────────────────────────────────────────


def test_create_transaction_type_mismatch_returns_400(client, cat_ids):
    """Posting an income transaction with an expense category must fail."""
    r = _make_tx(client, date_str="2026-05-01", amount=100_000, type_="income", category_id=cat_ids["expense"])
    assert r.status_code == 400
    assert "does not match" in r.json()["detail"]


def test_create_transaction_nonexistent_category_returns_404(client):
    r = _make_tx(client, date_str="2026-05-01", amount=100_000, type_="income", category_id=999999)
    assert r.status_code == 404


def test_update_nonexistent_transaction_returns_404(client):
    r = client.put("/api/transactions/999999", json={"amount": 1_000})
    assert r.status_code == 404


# ── Filters ───────────────────────────────────────────────────────────────────


def test_filter_by_category_id(client, cat_ids):
    _make_tx(client, date_str="2026-05-01", amount=1_000, type_="income", category_id=cat_ids["income"])
    _make_tx(client, date_str="2026-05-02", amount=2_000, type_="expense", category_id=cat_ids["expense"])
    r = client.get(f"/api/transactions/?category_id={cat_ids['income']}")
    assert r.status_code == 200
    assert all(t["category"]["id"] == cat_ids["income"] for t in r.json())


def test_filter_by_is_advance(client, cat_ids):
    _make_tx(
        client, date_str="2026-05-01", amount=1_000, type_="expense", category_id=cat_ids["expense"], is_advance=True
    )
    _make_tx(
        client, date_str="2026-05-02", amount=2_000, type_="expense", category_id=cat_ids["expense"], is_advance=False
    )
    r = client.get("/api/transactions/?is_advance=true")
    assert r.status_code == 200
    assert all(t["is_advance"] is True for t in r.json())


def test_filter_by_advance_settled(client, cat_ids):
    _make_tx(
        client,
        date_str="2026-05-01",
        amount=1_000,
        type_="expense",
        category_id=cat_ids["expense"],
        is_advance=True,
        advance_settled=True,
    )
    _make_tx(
        client,
        date_str="2026-05-02",
        amount=2_000,
        type_="expense",
        category_id=cat_ids["expense"],
        is_advance=True,
        advance_settled=False,
    )
    r = client.get("/api/transactions/?advance_settled=false")
    assert r.status_code == 200
    assert all(t["advance_settled"] is False for t in r.json())


def test_filter_by_source(client, cat_ids):
    _make_tx(
        client, date_str="2026-05-01", amount=1_000, type_="income", category_id=cat_ids["income"], source="manual"
    )
    _make_tx(
        client, date_str="2026-05-02", amount=2_000, type_="income", category_id=cat_ids["income"], source="import"
    )
    r = client.get("/api/transactions/?source=import")
    assert r.status_code == 200
    assert all(t["source"] == "import" for t in r.json())


def test_invalid_date_range_returns_400(client):
    r = client.get("/api/transactions/?start_date=2026-05-31&end_date=2026-05-01")
    assert r.status_code == 400
    assert "Start date" in r.json()["detail"]


# ── Stat endpoints ────────────────────────────────────────────────────────────


def test_monthly_summary_empty_db(client):
    r = client.get("/api/transactions/stats/monthly-summary?year=2026&month=5")
    assert r.status_code == 200
    d = r.json()
    assert d["income"] == pytest.approx(0)
    assert d["expense"] == pytest.approx(0)
    assert d["year"] == 2026
    assert d["month"] == 5


def test_monthly_summary_with_data(client, cat_ids):
    _make_tx(client, date_str="2026-05-10", amount=10_000_000, type_="income", category_id=cat_ids["income"])
    _make_tx(client, date_str="2026-05-15", amount=3_000_000, type_="expense", category_id=cat_ids["expense"])
    r = client.get("/api/transactions/stats/monthly-summary?year=2026&month=5")
    assert r.status_code == 200
    d = r.json()
    assert d["income"] == pytest.approx(10_000_000)
    assert d["expense"] == pytest.approx(3_000_000)
    assert d["net"] == pytest.approx(7_000_000)


def test_monthly_summary_defaults_to_current_month(client):
    r = client.get("/api/transactions/stats/monthly-summary")
    assert r.status_code == 200
    assert "year" in r.json() and "month" in r.json()


def test_by_category_returns_grouped_totals(client, cat_ids):
    _make_tx(client, date_str="2026-05-01", amount=2_000_000, type_="expense", category_id=cat_ids["expense"])
    _make_tx(client, date_str="2026-05-02", amount=1_000_000, type_="expense", category_id=cat_ids["expense"])
    r = client.get("/api/transactions/stats/by-category?type=expense&year=2026&month=5")
    assert r.status_code == 200
    result = r.json()
    assert len(result) >= 1
    assert result[0]["total"] == pytest.approx(3_000_000)


def test_by_category_empty_month(client):
    r = client.get("/api/transactions/stats/by-category?type=expense&year=2020&month=1")
    assert r.status_code == 200
    assert r.json() == []


# ── Bulk upload ───────────────────────────────────────────────────────────────


def test_bulk_upload_english_csv(client, cat_ids):
    csv_content = (
        "date,amount,type,category,description\n"
        "2026-05-01,1000000,income,Salary,May salary\n"
        "2026-05-02,500000,expense,Food,Lunch\n"
    )
    r = client.post(
        "/api/transactions/bulk-upload",
        files={"file": ("import.csv", io.BytesIO(csv_content.encode()), "text/csv")},
    )
    assert r.status_code == 200
    d = r.json()
    assert d["success"] is True
    assert d["stats"]["income"] == 1
    assert d["stats"]["expense"] == 1


def test_bulk_upload_skips_duplicates_across_calls(client, cat_ids):
    """A row already committed to the DB is skipped on a second upload call."""
    csv_content = "date,amount,type,category,description\n2026-05-01,1000000,income,Salary,May salary\n"
    client.post(
        "/api/transactions/bulk-upload",
        files={"file": ("import.csv", io.BytesIO(csv_content.encode()), "text/csv")},
    )
    # Second upload of the same row — should be skipped as duplicate
    r = client.post(
        "/api/transactions/bulk-upload",
        files={"file": ("import.csv", io.BytesIO(csv_content.encode()), "text/csv")},
    )
    assert r.status_code == 200
    d = r.json()
    assert d["stats"]["income"] == 0
    assert d["stats"]["skipped"] >= 1


def test_bulk_upload_invalid_date_skipped(client):
    csv_content = "date,amount,type,category\nnot-a-date,1000,income,Salary\n"
    r = client.post(
        "/api/transactions/bulk-upload",
        files={"file": ("import.csv", io.BytesIO(csv_content.encode()), "text/csv")},
    )
    assert r.status_code == 200
    assert r.json()["stats"]["skipped"] >= 1


def test_bulk_upload_missing_required_columns_returns_400(client):
    csv_content = "amount,type\n1000,income\n"
    r = client.post(
        "/api/transactions/bulk-upload",
        files={"file": ("import.csv", io.BytesIO(csv_content.encode()), "text/csv")},
    )
    assert r.status_code == 400
    assert "Missing required columns" in r.json()["error"]


def test_bulk_upload_empty_file_returns_400(client):
    r = client.post(
        "/api/transactions/bulk-upload",
        files={"file": ("empty.csv", io.BytesIO(b""), "text/csv")},
    )
    assert r.status_code == 400


def test_bulk_upload_vietnamese_csv(client):
    csv_content = "Năm,Tháng,Thu,Chi,Loại,Ghi chú\n2026,5,10000000,0,Lương,Tháng 5\n2026,5,0,3000000,Ăn uống,Bữa trưa\n"
    r = client.post(
        "/api/transactions/bulk-upload",
        files={"file": ("vn.csv", io.BytesIO(csv_content.encode("utf-8")), "text/csv")},
    )
    assert r.status_code == 200
    d = r.json()
    assert d["success"] is True
    assert d["stats"]["income"] == 1
    assert d["stats"]["expense"] == 1


def test_bulk_upload_invalid_type_skipped(client):
    csv_content = "date,amount,type,category\n2026-05-01,1000,badtype,Salary\n"
    r = client.post(
        "/api/transactions/bulk-upload",
        files={"file": ("import.csv", io.BytesIO(csv_content.encode()), "text/csv")},
    )
    assert r.status_code == 200
    assert r.json()["stats"]["skipped"] >= 1


def test_bulk_upload_zero_amount_skipped(client):
    csv_content = "date,amount,type,category\n2026-05-01,0,income,Salary\n"
    r = client.post(
        "/api/transactions/bulk-upload",
        files={"file": ("import.csv", io.BytesIO(csv_content.encode()), "text/csv")},
    )
    assert r.status_code == 200
    assert r.json()["stats"]["skipped"] >= 1


def test_bulk_upload_alternative_column_names(client):
    """Accepts 'transaction_date' and 'amount_vnd' as alternative column names."""
    csv_content = "transaction_date,amount_vnd,transaction_type,category_name\n2026-05-01,500000,income,Salary\n"
    r = client.post(
        "/api/transactions/bulk-upload",
        files={"file": ("import.csv", io.BytesIO(csv_content.encode()), "text/csv")},
    )
    assert r.status_code == 200
    assert r.json()["stats"]["income"] == 1


def test_bulk_upload_invalid_amount_format_skipped(client):
    csv_content = "date,amount,type,category\n2026-05-01,not_a_number,income,Salary\n"
    r = client.post(
        "/api/transactions/bulk-upload",
        files={"file": ("import.csv", io.BytesIO(csv_content.encode()), "text/csv")},
    )
    assert r.status_code == 200
    assert r.json()["stats"]["skipped"] >= 1


def test_bulk_upload_empty_category_skipped(client):
    csv_content = "date,amount,type,category\n2026-05-01,1000,income,\n"
    r = client.post(
        "/api/transactions/bulk-upload",
        files={"file": ("import.csv", io.BytesIO(csv_content.encode()), "text/csv")},
    )
    assert r.status_code == 200
    assert r.json()["stats"]["skipped"] >= 1


def test_bulk_upload_invalid_payment_method_defaults_to_cash(client):
    """Invalid payment_method is silently normalized to 'cash'."""
    csv_content = "date,amount,type,category,payment_method\n2026-05-01,500000,income,Salary,carrier_pigeon\n"
    r = client.post(
        "/api/transactions/bulk-upload",
        files={"file": ("import.csv", io.BytesIO(csv_content.encode()), "text/csv")},
    )
    assert r.status_code == 200
    assert r.json()["stats"]["income"] == 1


def test_bulk_upload_non_utf8_file_returns_400(client):
    """Bytes that cannot be decoded as UTF-8 return a 400 error."""
    invalid_bytes = b"\xff\xfe\x80\x81\x82"
    r = client.post(
        "/api/transactions/bulk-upload",
        files={"file": ("bad.csv", io.BytesIO(invalid_bytes), "text/csv")},
    )
    assert r.status_code == 400


def test_bulk_upload_vietnamese_missing_required_column(client):
    """Vietnamese CSV without 'Năm' column returns 400."""
    csv_content = "Tháng,Thu,Chi,Loại\n5,10000000,0,Lương\n"
    r = client.post(
        "/api/transactions/bulk-upload",
        files={"file": ("vn.csv", io.BytesIO(csv_content.encode("utf-8")), "text/csv")},
    )
    assert r.status_code == 400
    assert "Missing required columns" in r.json()["error"]


def test_bulk_upload_vietnamese_missing_year_skipped(client):
    """Row without year/month is skipped."""
    csv_content = "Năm,Tháng,Thu,Chi,Loại\n,,10000000,0,Lương\n"
    r = client.post(
        "/api/transactions/bulk-upload",
        files={"file": ("vn.csv", io.BytesIO(csv_content.encode("utf-8")), "text/csv")},
    )
    assert r.status_code == 200
    assert r.json()["stats"]["skipped"] >= 1


def test_bulk_upload_vietnamese_missing_category_error(client):
    """Row with empty category name logs an error and is skipped."""
    csv_content = "Năm,Tháng,Thu,Chi,Loại\n2026,5,10000000,0,\n"
    r = client.post(
        "/api/transactions/bulk-upload",
        files={"file": ("vn.csv", io.BytesIO(csv_content.encode("utf-8")), "text/csv")},
    )
    assert r.status_code == 200
    assert r.json()["stats"]["skipped"] >= 1


def test_bulk_upload_vietnamese_zero_amounts_skipped(client):
    """Row with both Thu=0 and Chi=0 is skipped."""
    csv_content = "Năm,Tháng,Thu,Chi,Loại\n2026,5,0,0,Tiết kiệm\n"
    r = client.post(
        "/api/transactions/bulk-upload",
        files={"file": ("vn.csv", io.BytesIO(csv_content.encode("utf-8")), "text/csv")},
    )
    assert r.status_code == 200
    assert r.json()["stats"]["skipped"] >= 1


def test_bulk_upload_vietnamese_skips_duplicate_income(client):
    """Vietnamese duplicate income is skipped on second call."""
    csv_content = "Năm,Tháng,Thu,Chi,Loại\n2026,5,10000000,0,Lương\n"
    client.post(
        "/api/transactions/bulk-upload",
        files={"file": ("vn.csv", io.BytesIO(csv_content.encode("utf-8")), "text/csv")},
    )
    r = client.post(
        "/api/transactions/bulk-upload",
        files={"file": ("vn.csv", io.BytesIO(csv_content.encode("utf-8")), "text/csv")},
    )
    assert r.status_code == 200
    assert r.json()["stats"]["skipped"] >= 1


def test_by_category_defaults_to_current_month(client):
    """stats/by-category without year/month params uses today."""
    r = client.get("/api/transactions/stats/by-category?type=expense")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_create_transaction_with_inline_savings_bundle(client, cat_ids):
    """POSTing with savings_bundle creates the bundle and links it."""
    r = client.post(
        "/api/transactions/",
        json={
            "date": "2026-05-01",
            "amount": 10_000_000,
            "type": "expense",
            "category_id": cat_ids["expense"],
            "payment_method": "bank_transfer",
            "is_savings_related": True,
            "savings_bundle": {
                "name": "Inline Bundle",
                "bank_name": "VCB",
                "type": "fixed_deposit",
                "initial_deposit": 10_000_000,
                "future_amount": 10_500_000,
                "interest_rate": 5.0,
                "start_date": "2026-05-01",
                "maturity_date": "2026-11-01",
            },
        },
    )
    assert r.status_code == 200
    d = r.json()
    assert d["is_savings_related"] is True
    assert d["savings_bundle_id"] is not None
