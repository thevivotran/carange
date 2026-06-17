"""
VietcomBank transaction parser.

Table-style layout:
  - "Số tiền GD" column for amount, "Nội dung GD" for description
  - Date in leftmost column
  - "CR" / "DR" suffix determines income / expense
"""

import re
from datetime import date
from typing import List, Optional

from ocr_worker.parsers.base import (
    BaseParser,
    group_rows,
    row_text,
    parse_vnd,
    parse_date,
    mean_confidence,
)
from ocr_worker.types import TextBlock, ParsedTransaction

_CR_RE = re.compile(r"\bCR\b")
_DR_RE = re.compile(r"\bDR\b")


class VietcomBankParser(BaseParser):
    def parse(self, blocks: List[TextBlock]) -> List[ParsedTransaction]:
        rows = group_rows(blocks)
        transactions: List[ParsedTransaction] = []
        current_date: Optional[date] = None

        for row in rows:
            text = row_text(row).strip()
            if not text:
                continue

            d = parse_date(text)
            if d and not parse_vnd(text):
                current_date = d
                continue

            result = parse_vnd(text)
            if not result:
                continue

            amount, tx_type = result

            if _CR_RE.search(text):
                tx_type = "income"
            elif _DR_RE.search(text):
                tx_type = "expense"

            desc = _CR_RE.sub("", text)
            desc = _DR_RE.sub("", desc)
            desc = re.sub(r"\d{1,3}(?:[.,]\d{3})+(?:\s*[₫đ])?", "", desc).strip()
            desc = desc or "VietcomBank"

            transactions.append(
                ParsedTransaction(
                    date=current_date or date.today(),
                    amount=amount,
                    tx_type=tx_type,
                    description=desc,
                    confidence=min(mean_confidence(row) * 0.9, 1.0),
                    raw_text=text,
                )
            )

        return transactions
