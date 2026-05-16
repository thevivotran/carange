"""
Shopee "Đơn đã mua" (delivered orders) parser.

Real OCR layout per order block:
  - Header row:  [badge] ShopName  Hoàn thành       ← order boundary
  - Product row: Product name truncated with ...     ← first long text after header
  - Variant row: color/size (short, skip)
  - Quantity:    x1
  - Prices:      old_priceđ new_priceđ (two numbers, skip)
  - TOTAL row:   Tổng số tiền (N sản phẩm): 308.074đ ← key signal
  - Noise:       review prompt, action buttons

Amounts use Vietnamese dot thousands-separator (308.074 = 308,074 VND).
Date is always today (screenshots don't contain the order date).
"""
import re
from datetime import date
from typing import List, Optional

from ocr_worker.parsers.base import BaseParser, group_rows, row_text, mean_confidence
from ocr_worker.types import TextBlock, ParsedTransaction

# "Hoàn thành" in various OCR renderings (accented + unaccented) — order boundary
_HOAN_THANH_RE = re.compile(r'ho\S*\s+th\S*nh|hoan\s*thanh', re.IGNORECASE)

# "Tổng số tiền ... : AMOUNT" — match "ti___n" loosely to handle full Vietnamese
# and OCR-garbled variants ("tién", "tien", "tiền", etc.)
_TOTAL_RE = re.compile(
    r'ti\S*n[^:]*:\s*(\d{1,3}(?:[.,]\d{3})+)',
    re.IGNORECASE,
)

# Two price-like numbers on the same line → price comparison row (skip)
_PRICE_PAIR_RE = re.compile(r'\d{1,3}[.,]\d{3}.*\d{1,3}[.,]\d{3}')


def _parse_amount(raw: str) -> int:
    """Parse Vietnamese dot-separated VND integer: '308.074' → 308074."""
    return int(raw.replace('.', '').replace(',', ''))


def _is_product_name(text: str) -> bool:
    """Long text that isn't a price-comparison or quantity row."""
    stripped = text.strip()
    if len(stripped) < 15:
        return False
    if _PRICE_PAIR_RE.search(stripped):
        return False
    return True


class ShopeeParser(BaseParser):

    def parse(self, blocks: List[TextBlock]) -> List[ParsedTransaction]:
        rows = group_rows(blocks)
        transactions: List[ParsedTransaction] = []

        in_order = False
        pending_product: Optional[str] = None

        for row in rows:
            text = row_text(row).strip()
            if not text:
                continue

            # ── Order boundary: new "Hoàn thành" header ───────────────────────
            if _HOAN_THANH_RE.search(text):
                in_order = True
                pending_product = None
                continue

            if not in_order:
                continue

            # ── Total line → emit transaction ─────────────────────────────────
            m = _TOTAL_RE.search(text)
            if m:
                amount = _parse_amount(m.group(1))
                desc = pending_product or "Shopee"
                conf = min(mean_confidence(row) * 0.95, 1.0)
                transactions.append(ParsedTransaction(
                    date=date.today(),
                    amount=amount,
                    tx_type="expense",
                    description=desc,
                    confidence=conf,
                    raw_text=text,
                    category_hint="Đồ dùng",
                ))
                in_order = False
                pending_product = None
                continue

            # ── Product name: first qualifying long text after header ─────────
            if pending_product is None and _is_product_name(text):
                pending_product = text

        return transactions
