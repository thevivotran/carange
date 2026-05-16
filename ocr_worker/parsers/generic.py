"""
Generic transaction parser — layout-agnostic fallback.

Strategy: scan every row for (date, amount) signals.
When a row contains an amount, look backward up to 3 rows for a date
and accumulate adjacent non-amount rows as the description.
Confidence is lower than source-specific parsers.
"""
from datetime import date
from typing import List, Optional

from ocr_worker.parsers.base import BaseParser, group_rows, row_text, parse_vnd, parse_date, mean_confidence
from ocr_worker.types import TextBlock, ParsedTransaction

_LOOKBACK = 3   # rows to scan backwards for a date


class GenericParser(BaseParser):

    def parse(self, blocks: List[TextBlock]) -> List[ParsedTransaction]:
        rows = group_rows(blocks)
        transactions: List[ParsedTransaction] = []
        row_texts = [row_text(r) for r in rows]

        last_date: Optional[date] = None
        desc_lines: List[str] = []

        for i, (row, text) in enumerate(zip(rows, row_texts)):
            stripped = text.strip()
            if not stripped:
                continue

            # Check for a standalone date line
            d = parse_date(stripped)
            if d and not parse_vnd(stripped):
                last_date = d
                desc_lines.clear()
                continue

            result = parse_vnd(stripped)
            if not result:
                desc_lines.append(stripped)
                continue

            amount, tx_type = result

            # Look backward for a date if we don't have one
            effective_date = last_date
            if effective_date is None:
                for back in range(1, min(_LOOKBACK + 1, i + 1)):
                    d = parse_date(row_texts[i - back])
                    if d:
                        effective_date = d
                        break

            if effective_date is None:
                desc_lines.append(stripped)
                continue

            description = " ".join(desc_lines).strip() or stripped
            desc_lines.clear()

            ocr_conf = mean_confidence(row)
            # Generic parser gets a confidence penalty — human review more likely
            confidence = min(ocr_conf * 0.65, 0.75)

            transactions.append(ParsedTransaction(
                date=effective_date,
                amount=amount,
                tx_type=tx_type,
                description=description,
                confidence=confidence,
                raw_text=stripped,
            ))

        return transactions
