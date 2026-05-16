"""
Timo bank transaction parser.

Real screenshot layout (chat-style):
  - Date header:  "Today" / "Yesterday" / "DD/MM/YYYY"
  - Per transaction:
      row A (y≈N):     [counterparty]               [±amount]   ← signed
      row B (y≈N+70):  [note / merchant name]       [balance]   ← unsigned balance, strip it
      row C (y≈N+160): [category label]                         ← left-aligned, skip
"""
import re
from datetime import date, timedelta
from typing import List, Optional

from ocr_worker.parsers.base import (
    BaseParser, group_rows, row_text, parse_vnd, parse_date, mean_confidence,
)
from ocr_worker.types import TextBlock, ParsedTransaction

_SKIP_RE = re.compile(
    r'^\s*('
    r'lịch sử|transaction list|transaction history|timo'
    r'|spend account|view all|hold to react|pending|transfer|transactions'
    r'|\d{2}:\d{2}(?::\d{2})?'
    r')\s*$',
    re.IGNORECASE,
)

_TODAY_RE     = re.compile(r'^\s*(?:today|hôm nay)\s*$',     re.IGNORECASE)
_YESTERDAY_RE = re.compile(r'^\s*(?:yesterday|hôm qua)\s*$', re.IGNORECASE)

# Strips the signed amount token so we can extract a clean description from row A
_SIGNED_TOKEN_RE = re.compile(r'[+\-]\s*\d{1,3}(?:[.,]\d{3})+(?:\s*[₫đ])?')

# Strips the unsigned running balance from row B (e.g. "1,022,428")
_BALANCE_RE = re.compile(r'\b\d{1,3}(?:[.,]\d{3})+\b')


def _signed_amount(text: str):
    """Return (amount, tx_type) only if text contains an explicit +/- sign."""
    if not re.search(r'[+\-]', text):
        return None
    return parse_vnd(text)


class TimoParser(BaseParser):

    def parse(self, blocks: List[TextBlock]) -> List[ParsedTransaction]:
        rows = group_rows(blocks)
        transactions: List[ParsedTransaction] = []
        current_date: Optional[date] = None

        # Pending transaction: waiting for row B to supply the real description
        pending: Optional[dict] = None

        def _flush(desc: str) -> None:
            if pending is None:
                return
            transactions.append(ParsedTransaction(
                date=pending['date'],
                amount=pending['amount'],
                tx_type=pending['tx_type'],
                description=desc or pending['fallback_desc'],
                confidence=pending['confidence'],
                raw_text=pending['raw_text'],
            ))

        for row in rows:
            text = row_text(row)
            stripped = text.strip()

            if not stripped:
                continue

            # ── Date markers ──────────────────────────────────────────────────
            if _TODAY_RE.match(stripped):
                if pending:
                    _flush(pending['fallback_desc']); pending = None
                current_date = date.today()
                continue
            if _YESTERDAY_RE.match(stripped):
                if pending:
                    _flush(pending['fallback_desc']); pending = None
                current_date = date.today() - timedelta(days=1)
                continue

            # Explicit date header (DD/MM or DD/MM/YYYY)
            d = parse_date(stripped)
            if d and not parse_vnd(stripped):
                if pending:
                    _flush(pending['fallback_desc']); pending = None
                current_date = d
                continue

            # ── Noise ─────────────────────────────────────────────────────────
            if _SKIP_RE.match(stripped):
                continue

            if current_date is None:
                continue

            # ── Row A: signed amount → start pending transaction ──────────────
            result = _signed_amount(stripped)
            if result:
                # Flush any previous pending (no row B was found between them)
                if pending:
                    _flush(pending['fallback_desc']); pending = None

                amount, tx_type = result
                fallback = _SIGNED_TOKEN_RE.sub('', stripped).strip() or stripped
                pending = {
                    'date': current_date,
                    'amount': amount,
                    'tx_type': tx_type,
                    'fallback_desc': fallback,
                    'confidence': min(mean_confidence(row) * 0.9, 1.0),
                    'raw_text': stripped,
                }
                continue

            # ── Row B: description + unsigned balance ─────────────────────────
            if pending is not None:
                desc = _BALANCE_RE.sub('', stripped).strip()
                _flush(desc)
                pending = None
                # don't continue — fall through would just skip it anyway

        # Flush any trailing pending
        if pending:
            _flush(pending['fallback_desc'])

        return transactions
