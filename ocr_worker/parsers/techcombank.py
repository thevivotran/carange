"""
Techcombank transaction parser.

Block layout per transaction:
  - Header row contains date (DD/MM/YYYY)
  - "Số tiền: AMOUNT" row
  - "Nội dung: DESCRIPTION" row
  - "Ghi nợ" / "Ghi có" label determines tx_type
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

_SO_TIEN_RE = re.compile(r"s[oố]\s*ti.+?n\s*:\s*(.+)", re.IGNORECASE)
_NOI_DUNG_RE = re.compile(r"n[oộ]i\s*dung\s*:\s*(.+)", re.IGNORECASE)
_GHI_NO_RE = re.compile(r"ghi\s*n[ơợ]", re.IGNORECASE)
_GHI_CO_RE = re.compile(r"ghi\s*c[oó]", re.IGNORECASE)


class TechcombankParser(BaseParser):
    def parse(self, blocks: List[TextBlock]) -> List[ParsedTransaction]:
        rows = group_rows(blocks)
        transactions: List[ParsedTransaction] = []
        current_date: Optional[date] = None
        pending_amount: Optional[float] = None
        pending_desc: Optional[str] = None
        pending_tx_type: Optional[str] = None
        pending_conf: float = 0.0
        pending_raw: str = ""

        def _flush():
            nonlocal pending_amount, pending_desc, pending_tx_type, pending_conf, pending_raw
            if pending_amount is not None and current_date is not None:
                transactions.append(
                    ParsedTransaction(
                        date=current_date,
                        amount=pending_amount,
                        tx_type=pending_tx_type or "expense",
                        description=pending_desc or "Techcombank",
                        confidence=min(pending_conf * 0.9, 1.0),
                        raw_text=pending_raw,
                    )
                )
            pending_amount = None
            pending_desc = None
            pending_tx_type = None
            pending_conf = 0.0
            pending_raw = ""

        for row in rows:
            text = row_text(row).strip()
            if not text:
                continue

            d = parse_date(text)
            if d and not parse_vnd(text):
                _flush()
                current_date = d
                continue

            m_tien = _SO_TIEN_RE.search(text)
            if m_tien:
                _flush()
                result = parse_vnd(m_tien.group(1))
                if result:
                    pending_amount = result[0]
                    pending_tx_type = result[1]
                    pending_conf = mean_confidence(row)
                    pending_raw = text
                continue

            m_noi_dung = _NOI_DUNG_RE.search(text)
            if m_noi_dung:
                pending_desc = m_noi_dung.group(1).strip()
                continue

            if _GHI_NO_RE.search(text):
                pending_tx_type = "expense"
                continue

            if _GHI_CO_RE.search(text):
                pending_tx_type = "income"
                continue

        _flush()
        return transactions
