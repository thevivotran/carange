"""
Parser and source-detector unit tests.

No PaddleOCR dependency — all tests feed pre-built TextBlock lists directly.
"""

import pytest
from datetime import date

from ocr_worker.types import TextBlock
from ocr_worker.parsers.base import parse_vnd, parse_date, group_rows, row_text
from ocr_worker.parsers.timo import TimoParser
from ocr_worker.parsers.shopee import ShopeeParser
from ocr_worker.parsers.grab import GrabParser
from ocr_worker.parsers.vpbank import VPBankParser
from ocr_worker.parsers.techcombank import TechcombankParser
from ocr_worker.parsers.mbbank import MBBankParser
from ocr_worker.parsers.vietcombank import VietcomBankParser
from ocr_worker.parsers.generic import GenericParser
from ocr_worker.source_detector import detect_source
from app.models.database import ImportSource


# ── TextBlock factory ──────────────────────────────────────────────────────────


def block(text: str, x: float = 0, y: float = 0, w: float = 200, h: float = 30, conf: float = 0.95) -> TextBlock:
    return TextBlock(text=text, confidence=conf, x=x, y=y, w=w, h=h)


def row_at(y: float, *texts: str) -> list[TextBlock]:
    """Build a horizontal row of blocks at the given y coordinate."""
    blocks = []
    x = 0.0
    for t in texts:
        blocks.append(block(t, x=x, y=y, w=len(t) * 12.0, h=30))
        x += len(t) * 12.0 + 10
    return blocks


# ── VND amount parsing ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text,expected_amount,expected_type",
    [
        ("-45,000", 45_000, "expense"),
        ("+1,500,000", 1_500_000, "income"),
        ("-1.500.000", 1_500_000, "expense"),
        ("1.500.000đ", 1_500_000, "income"),  # no sign → income (credit)
        ("₫125,000", 125_000, "income"),
        ("+15.000.000", 15_000_000, "income"),
        ("-89000", 89_000, "expense"),
        ("350,000", 350_000, "income"),
    ],
)
def test_parse_vnd(text, expected_amount, expected_type):
    result = parse_vnd(text)
    assert result is not None, f"parse_vnd({text!r}) returned None"
    amount, tx_type = result
    assert amount == expected_amount
    assert tx_type == expected_type


@pytest.mark.parametrize("text", ["abc", "", "ngày 15/05", "0"])
def test_parse_vnd_returns_none(text):
    assert parse_vnd(text) is None


# ── Date parsing ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text,expected",
    [
        ("15/05/2026", date(2026, 5, 15)),
        ("15-05-2026", date(2026, 5, 15)),
        ("2026-05-15", date(2026, 5, 15)),
        ("15 tháng 5 2026", date(2026, 5, 15)),
        ("Thứ Tư, 15/05/2026 12:30", date(2026, 5, 15)),
        ("giao dịch ngày 14/05/2026", date(2026, 5, 14)),
    ],
)
def test_parse_date(text, expected):
    assert parse_date(text) == expected


def test_parse_date_fallback_year():
    from datetime import date as _date

    result = parse_date("15/05", fallback_year=2026)
    assert result == _date(2026, 5, 15)


def test_parse_date_returns_none():
    assert parse_date("no date here") is None


# ── Row grouping ──────────────────────────────────────────────────────────────


def test_group_rows_single_row():
    blocks = [block("A", x=0, y=0), block("B", x=100, y=5)]  # within threshold
    rows = group_rows(blocks)
    assert len(rows) == 1
    assert len(rows[0]) == 2


def test_group_rows_multiple_rows():
    blocks = [
        block("A", x=0, y=0),
        block("B", x=0, y=100),
        block("C", x=0, y=200),
    ]
    rows = group_rows(blocks)
    assert len(rows) == 3


def test_group_rows_sorted_by_x():
    blocks = [block("B", x=200, y=0), block("A", x=0, y=0)]
    rows = group_rows(blocks)
    assert row_text(rows[0]) == "A B"


# ── Source detector ───────────────────────────────────────────────────────────


def _blocks_from_text(text: str) -> list[TextBlock]:
    return [block(line.strip()) for line in text.splitlines() if line.strip()]


@pytest.mark.parametrize(
    "text,expected_source",
    [
        ("Timo\nLịch sử giao dịch\n+1,500,000", ImportSource.TIMO),
        ("Don da mua\nShopee Mall\nHoan thanh\nTong so tien: 350.000d", ImportSource.SHOPEE),
        ("GrabFood\nMcDonald's\n₫125,000", ImportSource.GRAB),
        ("VPBank Smart\nSo du kha dung\n-45.000đ", ImportSource.VPBANK),
        ("Techcombank\nGhi nợ\nSố tiền: 200.000đ", ImportSource.TECHCOMBANK),
        ("MB Bank\nMB App\n-100.000đ", ImportSource.MBBANK),
        ("Vietcombank\nSố tiền GD\nCR 500.000đ", ImportSource.VIETCOMBANK),
    ],
)
def test_detect_source(text, expected_source):
    bs = _blocks_from_text(text)
    assert detect_source(bs) == expected_source


def test_detect_source_returns_none_for_unknown():
    bs = _blocks_from_text("Hello world\nSome random text")
    assert detect_source(bs) is None


# ── Timo parser ───────────────────────────────────────────────────────────────


def _timo_blocks():
    """Simulate a Timo history screenshot with 3 transactions."""
    all_blocks = []
    y = 0.0

    def add_row(*texts, dy=40):
        nonlocal y
        all_blocks.extend(row_at(y, *texts))
        y += dy

    add_row("Timo")
    add_row("Lịch sử giao dịch")
    add_row("15/05/2026")
    add_row("Highland Coffee", "-45,000")
    add_row("Grab", "-89,000")
    add_row("14/05/2026")
    add_row("Nhận tiền", "+500,000")
    return all_blocks


def test_timo_parser_count():
    txns = TimoParser().parse(_timo_blocks())
    assert len(txns) == 3


def test_timo_parser_expense():
    txns = TimoParser().parse(_timo_blocks())
    expenses = [t for t in txns if t.tx_type == "expense"]
    assert len(expenses) == 2
    amounts = {t.amount for t in expenses}
    assert 45_000 in amounts
    assert 89_000 in amounts


def test_timo_parser_income():
    txns = TimoParser().parse(_timo_blocks())
    incomes = [t for t in txns if t.tx_type == "income"]
    assert len(incomes) == 1
    assert incomes[0].amount == 500_000


def test_timo_parser_dates():
    txns = TimoParser().parse(_timo_blocks())
    dates = {t.date for t in txns}
    assert date(2026, 5, 15) in dates
    assert date(2026, 5, 14) in dates


def test_timo_parser_confidence():
    txns = TimoParser().parse(_timo_blocks())
    for t in txns:
        assert 0 < t.confidence <= 1.0


# ── Shopee parser ─────────────────────────────────────────────────────────────


def _shopee_blocks():
    """Mirrors the real 'Đơn đã mua' (delivered orders) screenshot layout."""
    all_blocks = []
    y = 0.0

    def add_row(*texts, dy=40):
        nonlocal y
        all_blocks.extend(row_at(y, *texts))
        y += dy

    # Order 1 — header row contains "Hoàn thành" as order status
    add_row("Shopee Mall", "Shop A", "Hoàn thành")
    add_row("Bot Cao Rau GILLETTE Huong Chanh san pham A")  # product name (len > 15)
    add_row("Variant description here")  # variant (also long — comes second)
    add_row("350.000d")  # per-item price
    add_row("Tổng số tiền (1 sản phẩm): 350.000đ")  # total line

    # Order 2
    add_row("Yeu thich+", "Shop B", "Hoàn thành")
    add_row("Bot cacao nguyen chat 100 phan tram san pham B")
    add_row("Tổng số tiền (1 sản phẩm): 125.000đ")
    return all_blocks


def test_shopee_parser_count():
    txns = ShopeeParser().parse(_shopee_blocks())
    assert len(txns) == 2


def test_shopee_all_expenses():
    txns = ShopeeParser().parse(_shopee_blocks())
    assert all(t.tx_type == "expense" for t in txns)


def test_shopee_amounts():
    txns = ShopeeParser().parse(_shopee_blocks())
    amounts = {t.amount for t in txns}
    assert 350_000 in amounts
    assert 125_000 in amounts


def test_shopee_category_hint():
    txns = ShopeeParser().parse(_shopee_blocks())
    assert all(t.category_hint == "Đồ dùng" for t in txns)


# ── Grab parser ───────────────────────────────────────────────────────────────


def _grab_blocks():
    """Mirrors the real Grab Activity History (Transport tab) layout."""
    all_blocks = []
    y = 0.0

    def add_row(*texts, dy=40):
        nonlocal y
        all_blocks.extend(row_at(y, *texts))
        y += dy

    # Transaction 1 — Row A: desc + amount, Row B: desc cont + GrabCoins, Row D: date
    add_row("Trung Tam Hoi Nghi Tiec Cuoi Riverside", "52.000d")
    add_row("Palace destination more details here", "+7 GrabCoins")
    add_row("16 May 2026,20:59")

    # Transaction 2
    add_row("Havanna Tower to Ham Nghi destination", "89.000d")
    add_row("Gate to full address line two here", "+16 GrabCoins")
    add_row("14 May 2026,08:15")
    return all_blocks


def test_grab_parser_count():
    txns = GrabParser().parse(_grab_blocks())
    assert len(txns) == 2


def test_grab_all_expenses():
    txns = GrabParser().parse(_grab_blocks())
    assert all(t.tx_type == "expense" for t in txns)


def test_grab_category_hint():
    txns = GrabParser().parse(_grab_blocks())
    assert all(t.category_hint == "Đi lại" for t in txns)


# ── VPBank parser ────────────────────────────────────────────────────────────


def _vpbank_blocks():
    all_blocks = []
    y = 0.0

    def add_row(*texts, dy=40):
        nonlocal y
        all_blocks.extend(row_at(y, *texts))
        y += dy

    add_row("VPBank Smart")
    add_row("15/05/2026 10:30")
    add_row("Chuyen tien", "-45.000đ")
    add_row("Số dư: 1.000.000đ")
    add_row("14/05/2026 08:00")
    add_row("Nhan luong", "+1.200.000đ")
    return all_blocks


def test_vpbank_parser_count():
    txns = VPBankParser().parse(_vpbank_blocks())
    assert len(txns) == 2


def test_vpbank_parser_types():
    txns = VPBankParser().parse(_vpbank_blocks())
    types = {t.tx_type for t in txns}
    assert "expense" in types
    assert "income" in types


def test_vpbank_parser_amounts():
    txns = VPBankParser().parse(_vpbank_blocks())
    amounts = {t.amount for t in txns}
    assert 45_000 in amounts
    assert 1_200_000 in amounts


# ── Techcombank parser ───────────────────────────────────────────────────────


def _techcombank_blocks():
    all_blocks = []
    y = 0.0

    def add_row(*texts, dy=40):
        nonlocal y
        all_blocks.extend(row_at(y, *texts))
        y += dy

    add_row("Techcombank")
    add_row("15/05/2026")
    add_row("Số tiền: 200.000đ")
    add_row("Nội dung: Thanh toan dien nuoc")
    add_row("Ghi nợ")
    add_row("16/05/2026")
    add_row("Số tiền: 5.000.000đ")
    add_row("Nội dung: Nhan luong")
    add_row("Ghi có")
    return all_blocks


def test_techcombank_parser_count():
    txns = TechcombankParser().parse(_techcombank_blocks())
    assert len(txns) == 2


def test_techcombank_parser_expense():
    txns = TechcombankParser().parse(_techcombank_blocks())
    expenses = [t for t in txns if t.tx_type == "expense"]
    assert len(expenses) == 1
    assert expenses[0].amount == 200_000


def test_techcombank_parser_income():
    txns = TechcombankParser().parse(_techcombank_blocks())
    incomes = [t for t in txns if t.tx_type == "income"]
    assert len(incomes) == 1
    assert incomes[0].amount == 5_000_000


# ── MB Bank parser ───────────────────────────────────────────────────────────


def _mbbank_blocks():
    all_blocks = []
    y = 0.0

    def add_row(*texts, dy=40):
        nonlocal y
        all_blocks.extend(row_at(y, *texts))
        y += dy

    add_row("MB Bank")
    add_row("15/05/2026")
    add_row("Mua ca phe", "-55.000đ")
    add_row("So du: 2.000.000đ")
    add_row("14/05/2026")
    add_row("Nhan tien", "+3.000.000đ")
    return all_blocks


def test_mbbank_parser_count():
    txns = MBBankParser().parse(_mbbank_blocks())
    assert len(txns) == 2


def test_mbbank_parser_amounts():
    txns = MBBankParser().parse(_mbbank_blocks())
    amounts = {t.amount for t in txns}
    assert 55_000 in amounts
    assert 3_000_000 in amounts


def test_mbbank_parser_confidence():
    txns = MBBankParser().parse(_mbbank_blocks())
    for t in txns:
        assert 0 < t.confidence <= 1.0


# ── VietcomBank parser ───────────────────────────────────────────────────────


def _vietcombank_blocks():
    all_blocks = []
    y = 0.0

    def add_row(*texts, dy=40):
        nonlocal y
        all_blocks.extend(row_at(y, *texts))
        y += dy

    add_row("Vietcombank")
    add_row("15/05/2026")
    add_row("Noi dung GD mua hang", "DR 150.000đ")
    add_row("16/05/2026")
    add_row("Noi dung GD nhan luong", "CR 10.000.000đ")
    return all_blocks


def test_vietcombank_parser_count():
    txns = VietcomBankParser().parse(_vietcombank_blocks())
    assert len(txns) == 2


def test_vietcombank_parser_types():
    txns = VietcomBankParser().parse(_vietcombank_blocks())
    types = {t.tx_type for t in txns}
    assert "expense" in types
    assert "income" in types


def test_vietcombank_parser_amounts():
    txns = VietcomBankParser().parse(_vietcombank_blocks())
    amounts = {t.amount for t in txns}
    assert 150_000 in amounts
    assert 10_000_000 in amounts


# ── Generic parser ────────────────────────────────────────────────────────────


def _generic_blocks():
    all_blocks = []
    y = 0.0

    def add_row(*texts, dy=40):
        nonlocal y
        all_blocks.extend(row_at(y, *texts))
        y += dy

    add_row("Some Bank")
    add_row("15/05/2026")
    add_row("Transfer out", "-200,000")
    add_row("14/05/2026")
    add_row("Salary credit", "+15,000,000")
    return all_blocks


def test_generic_parser_count():
    txns = GenericParser().parse(_generic_blocks())
    assert len(txns) == 2


def test_generic_parser_lower_confidence():
    specific = TimoParser().parse(_timo_blocks())
    generic = GenericParser().parse(_generic_blocks())
    avg_specific = sum(t.confidence for t in specific) / len(specific)
    avg_generic = sum(t.confidence for t in generic) / len(generic)
    assert avg_generic < avg_specific


# ── Processor integration (no PaddleOCR) ─────────────────────────────────────


def test_processor_full_pipeline(tmp_path, monkeypatch):
    """
    End-to-end: fake OCR output → processor commits transactions to DB.
    PaddleOCR is monkeypatched so this runs in CI without the heavy dep.
    """
    import struct
    import zlib
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models.database import Base, ImportJob, ImportJobStatus, ImportSource, Category

    engine = create_engine(f"sqlite:///{tmp_path}/t.db", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    SF = sessionmaker(bind=engine)

    # Seed categories
    with SF() as db:
        db.add(Category(name="Đồ dùng", type="expense", color="#EC4899", icon="shopping-bag", is_active=True))
        db.add(Category(name="Others", type="income", color="#6B7280", icon="circle", is_active=True))
        db.commit()

    # Create a real image file
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
    raw = zlib.compress(b"\x00\xff\xff\xff")
    idat = struct.pack(">I", len(raw)) + b"IDAT" + raw + struct.pack(">I", zlib.crc32(b"IDAT" + raw) & 0xFFFFFFFF)
    iend = b"\x00\x00\x00\x00IEND\xaeB\x60\x82"
    img = tmp_path / "test.png"
    img.write_bytes(sig + ihdr + idat + iend)

    # Fake OCR: return Shopee-like blocks matching the real "Đơn đã mua" layout
    fake_blocks = (
        row_at(0, "Shopee Mall", "Hoàn thành")
        + row_at(40, "San pham A la ten san pham rat dai va ro rang")
        + row_at(80, "Tổng số tiền (1 sản phẩm): 350.000đ")
    )
    monkeypatch.setattr("ocr_worker.ocr.extract_blocks", lambda _path: fake_blocks)

    with SF() as db:
        job = ImportJob(
            filename="shopee.png",
            file_path=str(img),
            image_hash="abc",
            status=ImportJobStatus.PROCESSING,
        )
        db.add(job)
        db.commit()
        db.refresh(job)

        from ocr_worker.processor import process_job

        process_job(job, db)
        db.refresh(job)

        assert job.status == ImportJobStatus.DONE
        assert job.transaction_count == 1
        assert job.detected_source == ImportSource.SHOPEE

    from app.models.database import Transaction

    with SF() as db:
        txns = db.query(Transaction).all()
        assert len(txns) == 1
        assert txns[0].amount == 350_000
        assert txns[0].source == "shopee"
        assert txns[0].import_job_id == job.id


def test_vpbank_parser_expense():
    all_blocks = []
    y = 0.0

    def add_row(*texts, dy=40):
        nonlocal y
        all_blocks.extend(row_at(y, *texts))
        y += dy

    add_row("VPBank Smart")
    add_row("15/05/2026 10:30")
    add_row("Chuyen tien", "-45.000đ")
    txns = VPBankParser().parse(all_blocks)
    expenses = [t for t in txns if t.tx_type == "expense"]
    assert len(expenses) == 1
    assert expenses[0].amount == 45000
    assert expenses[0].tx_type == "expense"


def test_mbbank_parser_expense():
    all_blocks = []
    y = 0.0

    def add_row(*texts, dy=40):
        nonlocal y
        all_blocks.extend(row_at(y, *texts))
        y += dy

    add_row("MB Bank")
    add_row("15/05/2026")
    add_row("Mua ca phe", "-200.000đ")
    txns = MBBankParser().parse(all_blocks)
    expenses = [t for t in txns if t.tx_type == "expense"]
    assert len(expenses) == 1
    assert expenses[0].amount == 200000
    assert expenses[0].tx_type == "expense"


def test_vietcombank_parser_both():
    all_blocks = []
    y = 0.0

    def add_row(*texts, dy=40):
        nonlocal y
        all_blocks.extend(row_at(y, *texts))
        y += dy

    add_row("Vietcombank")
    add_row("15/05/2026")
    add_row("Noi dung GD mua hang", "DR 150.000đ")
    add_row("16/05/2026")
    add_row("Noi dung GD nhan luong", "CR 10.000.000đ")
    txns = VietcomBankParser().parse(all_blocks)
    assert len(txns) == 2
    types = {t.tx_type for t in txns}
    assert "expense" in types
    assert "income" in types
    dr = [t for t in txns if t.tx_type == "expense"][0]
    cr = [t for t in txns if t.tx_type == "income"][0]
    assert dr.amount == 150000
    assert cr.amount == 10000000


def test_detect_vpbank():
    bs = _blocks_from_text("VPBank Smart\nSo du kha dung\n-45.000đ")
    assert detect_source(bs) == ImportSource.VPBANK


def test_detect_techcombank():
    bs = _blocks_from_text("Techcombank\nGhi nợ\nSố tiền: 200.000đ")
    assert detect_source(bs) == ImportSource.TECHCOMBANK


def test_detect_mbbank():
    bs = _blocks_from_text("MB Bank\nMB App\n-100.000đ")
    assert detect_source(bs) == ImportSource.MBBANK


def test_detect_vietcombank():
    bs = _blocks_from_text("Vietcombank\nSố tiền GD\nCR 500.000đ")
    assert detect_source(bs) == ImportSource.VIETCOMBANK
