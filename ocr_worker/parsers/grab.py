"""
Grab Activity History parser (Transport tab).

Real screenshot layout per transaction:
  Row A: Description line 1 (left)   Amount e.g. "52.000d"  (right)
  Row B: Description line 2 (left)   +N GrabCoins           (right)
  Row C: "Booked by Name"  (optional, skip)
  Row D: "DD Month YYYY,HH:MM"       (date trigger → emit)
  Row E: "Rate -> / Rebook ->"       (optional, skip)

Amount uses Vietnamese dot thousands-separator (52.000 = 52,000 VND).
All Grab transactions are expenses, category "Đi lại".
"""

import re
from datetime import date
from typing import List, Optional

from ocr_worker.parsers.base import BaseParser, group_rows, row_text, mean_confidence
from ocr_worker.types import TextBlock, ParsedTransaction

# "52.000d", "39.000g" — dot thousands separator, đ/d/g OCR variants
_AMOUNT_RE = re.compile(r"(\d{1,3}[.,]\d{3})[dđ@g]", re.IGNORECASE)
# "+7 GrabCoins" — confirmation the row belongs to a real transaction
_GRABCOINS_RE = re.compile(r"\+\d+\s*grab\s*coins", re.IGNORECASE)
# Rows to discard entirely
_NOISE_RE = re.compile(
    r"^(?:booked\s+by|rate\s*->|rebook|activity\s+history"
    r"|transport|food|mart|dine\s+out|\d{2}:\d{2})",
    re.IGNORECASE,
)

_MONTHS_EN = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}
_EN_DATE_RE = re.compile(
    r"(\d{1,2})\s*(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\s*(\d{4})",
    re.IGNORECASE,
)


def _parse_grab_date(text: str) -> Optional[date]:
    m = _EN_DATE_RE.search(text)
    if not m:
        return None
    return date(int(m.group(3)), _MONTHS_EN[m.group(2).lower()[:3]], int(m.group(1)))


class GrabParser(BaseParser):
    def parse(self, blocks: List[TextBlock]) -> List[ParsedTransaction]:
        rows = group_rows(blocks)
        transactions: List[ParsedTransaction] = []
        pending: Optional[dict] = None

        for row in rows:
            text = row_text(row).strip()
            if not text:
                continue

            if _NOISE_RE.search(text):
                continue

            # ── Row A: description line 1 + amount ────────────────────────────
            m = _AMOUNT_RE.search(text)
            if m:
                amount = int(m.group(1).replace(".", "").replace(",", ""))
                desc1 = _AMOUNT_RE.sub("", text).strip()
                pending = {"amount": amount, "desc_parts": [desc1] if desc1 else []}
                continue

            if pending is None:
                continue

            # ── Row B: description line 2 + GrabCoins ─────────────────────────
            if _GRABCOINS_RE.search(text):
                desc2 = _GRABCOINS_RE.sub("", text).strip()
                if desc2:
                    pending["desc_parts"].append(desc2)
                continue

            # ── Row D: date → emit transaction ────────────────────────────────
            d = _parse_grab_date(text)
            if d:
                desc = " ".join(pending["desc_parts"]).strip() or "Grab"
                transactions.append(
                    ParsedTransaction(
                        date=d,
                        amount=pending["amount"],
                        tx_type="expense",
                        description=desc,
                        confidence=min(mean_confidence(row) * 0.9, 1.0),
                        raw_text=text,
                        category_hint="Đi lại",
                    )
                )
                pending = None

        return transactions
