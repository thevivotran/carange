"""
MB Bank transaction parser.

Chat-style layout similar to Timo:
  - Signed amount on one row, description + balance on next row
  - Date may appear as section header (HH:mm DD/MM/YYYY or ISO)
  - Skip rows matching "Số dư" or "SD:" (balance labels)
"""

import re
from datetime import date, timedelta
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

_SKIP_RE = re.compile(
    r"^\s*("
    r"s[oố]\s*d[ưư]|sd\s*:|mb\s*bank|lịch\s*sử|transaction"
    r"|\d{2}:\d{2}(?::\d{2})?\s*$"
    r")",
    re.IGNORECASE,
)

_TODAY_RE = re.compile(r"^\s*(?:today|hôm nay)\s*$", re.IGNORECASE)
_YESTERDAY_RE = re.compile(r"^\s*(?:yesterday|hôm qua)\s*$", re.IGNORECASE)
_SIGNED_TOKEN_RE = re.compile(r"[+\-]\s*\d{1,3}(?:[.,]\d{3})+(?:\s*[₫đ])?")
_BALANCE_RE = re.compile(r"\b\d{1,3}(?:[.,]\d{3})+\b")


def _signed_amount(text: str):
    if not re.search(r"[+\-]", text):
        return None
    return parse_vnd(text)


class MBBankParser(BaseParser):
    def parse(self, blocks: List[TextBlock]) -> List[ParsedTransaction]:
        rows = group_rows(blocks)
        transactions: List[ParsedTransaction] = []
        current_date: Optional[date] = None
        pending: Optional[dict] = None

        def _flush(desc: str) -> None:
            if pending is None:
                return
            transactions.append(
                ParsedTransaction(
                    date=pending["date"],
                    amount=pending["amount"],
                    tx_type=pending["tx_type"],
                    description=desc or pending["fallback_desc"],
                    confidence=pending["confidence"],
                    raw_text=pending["raw_text"],
                )
            )

        for row in rows:
            text = row_text(row).strip()
            if not text:
                continue

            if _SKIP_RE.match(text):
                continue

            if _TODAY_RE.match(text):
                if pending:
                    _flush(pending["fallback_desc"])
                    pending = None
                current_date = date.today()
                continue
            if _YESTERDAY_RE.match(text):
                if pending:
                    _flush(pending["fallback_desc"])
                    pending = None
                current_date = date.today() - timedelta(days=1)
                continue

            d = parse_date(text)
            if d and not parse_vnd(text):
                if pending:
                    _flush(pending["fallback_desc"])
                    pending = None
                current_date = d
                continue

            if current_date is None:
                continue

            result = _signed_amount(text)
            if result:
                if pending:
                    _flush(pending["fallback_desc"])
                    pending = None
                amount, tx_type = result
                fallback = _SIGNED_TOKEN_RE.sub("", text).strip() or text
                pending = {
                    "date": current_date,
                    "amount": amount,
                    "tx_type": tx_type,
                    "fallback_desc": fallback,
                    "confidence": min(mean_confidence(row) * 0.9, 1.0),
                    "raw_text": text,
                }
                continue

            if pending is not None:
                desc = _BALANCE_RE.sub("", text).strip()
                _flush(desc)
                pending = None

        if pending:
            _flush(pending["fallback_desc"])

        return transactions
