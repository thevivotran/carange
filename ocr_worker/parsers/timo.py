"""
Timo bank transaction history parser.

Timo's history view renders one transaction per logical block:
  - Date appears as a group header (DD/MM or DD/MM/YYYY)
  - Each transaction row: [time?]  description  [± amount]
  - Amount is right-aligned; positive = credit (green), negative = debit (red)

We sort all rows top-to-bottom, carry the most recent date header forward,
and extract transactions whenever we find an amount.
"""
import re
from datetime import date
from typing import List, Optional

from ocr_worker.parsers.base import BaseParser, Row, group_rows, row_text, parse_vnd, parse_date, mean_confidence
from ocr_worker.types import TextBlock, ParsedTransaction

# Timo date header looks like "15/05" or "15/05/2026" on a line by itself
_DATE_HEADER_RE = re.compile(r'^\s*\d{1,2}[/\-]\d{1,2}(?:[/\-]\d{4})?\s*$')

# Noise rows to skip
_SKIP_RE = re.compile(
    r'^\s*('
    r'lịch sử|giao dịch|transaction|history|timo|số dư|balance'
    r'|hôm nay|hôm qua|yesterday|today'
    r'|\d{2}:\d{2}(?::\d{2})?'   # lone time stamps like "15:30"
    r')\s*$',
    re.IGNORECASE,
)


class TimoParser(BaseParser):

    def parse(self, blocks: List[TextBlock]) -> List[ParsedTransaction]:
        rows = group_rows(blocks)
        transactions: List[ParsedTransaction] = []
        current_date: Optional[date] = None
        desc_buffer: List[str] = []

        for row in rows:
            text = row_text(row)
            stripped = text.strip()

            if not stripped:
                continue

            # ── Date header ───────────────────────────────────────────────────
            if _DATE_HEADER_RE.match(stripped):
                parsed = parse_date(stripped)
                if parsed:
                    current_date = parsed
                    desc_buffer.clear()
                    continue

            # ── Noise ─────────────────────────────────────────────────────────
            if _SKIP_RE.match(stripped):
                continue

            # ── Amount extraction ─────────────────────────────────────────────
            result = parse_vnd(stripped)
            if result and current_date:
                amount, tx_type = result
                description = " ".join(desc_buffer).strip() or stripped
                desc_buffer.clear()

                # Confidence: OCR quality × field completeness
                ocr_conf = mean_confidence(row)
                field_conf = 0.5 + (0.3 if current_date else 0) + (0.2 if description else 0)
                confidence = min(ocr_conf * field_conf, 1.0)

                transactions.append(ParsedTransaction(
                    date=current_date,
                    amount=amount,
                    tx_type=tx_type,
                    description=description,
                    confidence=confidence,
                    raw_text=stripped,
                    category_hint="Transportation" if tx_type == "expense" else None,
                ))
                continue

            # ── Accumulate description lines ──────────────────────────────────
            # Inline date on same row as description (e.g. "15/05  Coffee Shop")
            inline_date = parse_date(stripped)
            if inline_date:
                current_date = inline_date
                # Strip the date portion and keep the rest as description
                remainder = re.sub(r'\d{1,2}[/\-]\d{1,2}(?:[/\-]\d{4})?', '', stripped).strip()
                if remainder:
                    desc_buffer = [remainder]
                else:
                    desc_buffer.clear()
            else:
                desc_buffer.append(stripped)

        return transactions
