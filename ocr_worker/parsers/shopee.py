"""
Shopee order history parser.

Shopee's order list groups each order as:
  [Shop name]
  Đã giao  /  Đã huỷ  /  ...  (status)
  [date: DD Tháng M YYYY or DD/MM/YYYY]
  [item lines]
  Tổng đơn hàng: ₫XXX,XXX   ← total we extract

All Shopee transactions are expenses.
"""
import re
from datetime import date
from typing import List, Optional

from ocr_worker.parsers.base import BaseParser, group_rows, row_text, parse_vnd, parse_date, mean_confidence
from ocr_worker.types import TextBlock, ParsedTransaction

_TOTAL_RE  = re.compile(r'tổng\s+(?:đơn\s+hàng|tiền)', re.IGNORECASE)
_STATUS_RE = re.compile(r'đã\s+(?:giao|huỷ|hoàn|hủy)|chờ\s+xác\s+nhận', re.IGNORECASE)
_SHOPEE_NOISE = re.compile(
    r'^\s*(?:shopee|shop|đánh giá|xem\s+chi\s+tiết|liên\s+hệ|đặt\s+lại)\s*$',
    re.IGNORECASE,
)


class ShopeeParser(BaseParser):

    def parse(self, blocks: List[TextBlock]) -> List[ParsedTransaction]:
        rows = group_rows(blocks)
        transactions: List[ParsedTransaction] = []

        current_shop: Optional[str] = None
        current_date: Optional[date] = None

        for row in rows:
            text = row_text(row).strip()
            if not text:
                continue

            # ── Order status line resets the block context ────────────────────
            if _STATUS_RE.search(text):
                current_shop = None
                current_date = None
                continue

            # ── Date line ─────────────────────────────────────────────────────
            parsed_date = parse_date(text)
            if parsed_date and not parse_vnd(text):
                current_date = parsed_date
                continue

            # ── Total line → create transaction ──────────────────────────────
            if _TOTAL_RE.search(text):
                result = parse_vnd(text)
                if result and current_date:
                    amount, _ = result
                    desc = current_shop or "Shopee"
                    ocr_conf = mean_confidence(row)
                    conf = min(ocr_conf * 0.95, 1.0)   # Shopee totals are very reliable
                    transactions.append(ParsedTransaction(
                        date=current_date,
                        amount=amount,
                        tx_type="expense",
                        description=desc,
                        confidence=conf,
                        raw_text=text,
                        category_hint="Shopping",
                    ))
                    current_shop = None
                    current_date = None
                continue

            # ── Noise ─────────────────────────────────────────────────────────
            if _SHOPEE_NOISE.match(text):
                continue

            # ── Shop name (first non-noise line before a date) ────────────────
            if current_shop is None and current_date is None and len(text) > 2:
                current_shop = text

        return transactions
