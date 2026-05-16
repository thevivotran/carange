"""
Grab receipt parser (GrabFood, GrabCar, GrabBike, GrabExpress).

Each receipt block looks like:
  GrabFood                     ← service type
  Thứ Tư, 15/05/2026 12:30    ← date + time
  [merchant / driver name]
  Subtotal / Tổng tiền  ₫XXX  ← or just a bare amount line
  ₫89,000                      ← fare / total

All Grab transactions are expenses.
"""
import re
from datetime import date
from typing import List, Optional

from ocr_worker.parsers.base import BaseParser, group_rows, row_text, parse_vnd, parse_date, mean_confidence
from ocr_worker.types import TextBlock, ParsedTransaction

_SERVICE_RE   = re.compile(r'\bgrab(?:food|car|bike|express|mart)?\b', re.IGNORECASE)
_TOTAL_RE     = re.compile(r'(?:tổng\s+tiền|subtotal|total|thành\s+tiền)', re.IGNORECASE)
_GRAB_NOISE   = re.compile(
    r'^\s*(?:grab|đánh giá|xem\s+chi\s+tiết|liên\s+hệ|giao\s+hàng|đặt\s+lại'
    r'|thứ\s+\w+|chủ\s+nhật)\s*$',
    re.IGNORECASE,
)


class GrabParser(BaseParser):

    def parse(self, blocks: List[TextBlock]) -> List[ParsedTransaction]:
        rows = group_rows(blocks)
        transactions: List[ParsedTransaction] = []

        current_service: Optional[str] = None
        current_date:    Optional[date] = None
        current_merchant: Optional[str] = None
        last_amount: Optional[tuple[float, str]] = None

        def _flush():
            nonlocal current_service, current_date, current_merchant, last_amount
            if last_amount and current_date:
                amount, tx_type = last_amount
                desc = current_merchant or current_service or "Grab"
                transactions.append(ParsedTransaction(
                    date=current_date,
                    amount=amount,
                    tx_type="expense",
                    description=desc,
                    confidence=0.85,
                    raw_text="",
                    category_hint="Transportation",
                ))
            current_service = None
            current_date = None
            current_merchant = None
            last_amount = None

        for row in rows:
            text = row_text(row).strip()
            if not text:
                continue

            # New service block starts (flush previous)
            if _SERVICE_RE.match(text):
                _flush()
                current_service = text
                continue

            # Date line
            parsed_date = parse_date(text)
            if parsed_date and not parse_vnd(text):
                current_date = parsed_date
                continue

            # Total / fare line
            if _TOTAL_RE.search(text) or (current_service and parse_vnd(text)):
                result = parse_vnd(text)
                if result:
                    last_amount = result
                continue

            if _GRAB_NOISE.match(text):
                continue

            # Merchant / destination name
            if current_service and current_date and not current_merchant:
                current_merchant = text

        _flush()   # handle last block
        return transactions
