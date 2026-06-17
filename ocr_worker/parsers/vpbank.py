"""
VPBank Smart transaction parser.

Chat-style list layout:
  - Each transaction row: signed amount (e.g. "-45.000đ" or "+1.200.000đ")
    + description + date-time (DD/MM/YYYY HH:mm)
  - "Số dư" rows (running balance) — skip
  - tx_type from sign: "-" → expense, "+" → income
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

_SO_DU_RE = re.compile(r"s[oố]\s*d[ưư]", re.IGNORECASE)
_SIGNED_TOKEN_RE = re.compile(r"[+\-]\s*\d{1,3}(?:[.,]\d{3})+(?:\s*[₫đ])?")


class VPBankParser(BaseParser):
    def parse(self, blocks: List[TextBlock]) -> List[ParsedTransaction]:
        rows = group_rows(blocks)
        transactions: List[ParsedTransaction] = []
        current_date: Optional[date] = None

        for row in rows:
            text = row_text(row).strip()
            if not text:
                continue

            if _SO_DU_RE.search(text):
                continue

            d = parse_date(text)
            if d and not parse_vnd(text):
                current_date = d
                continue

            result = parse_vnd(text)
            if not result:
                continue

            amount, tx_type = result
            desc = _SIGNED_TOKEN_RE.sub("", text).strip() or "VPBank"

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
