"""Vietcombank transaction notification email parser."""

import re
from datetime import datetime
from typing import Optional

from email_worker.parsers.base import BaseEmailParser, ParsedEmailTransaction


class VCBParser(BaseEmailParser):
    SENDER_PATTERN = re.compile(r"vcb|vietcombank", re.IGNORECASE)
    SUBJECT_PATTERN = re.compile(r"thông báo|giao dịch|biến động số dư", re.IGNORECASE)

    # VCB email body patterns (adjust based on actual email format)
    AMOUNT_DEBIT = re.compile(r"(?:số tiền|amount|chi|ghi nợ)[:\s]+([0-9.,]+)\s*(?:VND|đ|VNĐ)", re.IGNORECASE)
    AMOUNT_CREDIT = re.compile(r"(?:ghi có|nhận)[:\s]+([0-9.,]+)\s*(?:VND|đ|VNĐ)", re.IGNORECASE)
    DATE_PATTERN = re.compile(r"(\d{2})[/-](\d{2})[/-](\d{4})")
    DESC_PATTERN = re.compile(r"(?:nội dung|mô tả|description)[:\s]+(.+?)(?:\n|$)", re.IGNORECASE)

    def can_parse(self, sender: str, subject: str, body_text: str) -> bool:
        return bool(self.SENDER_PATTERN.search(sender)) or bool(self.SUBJECT_PATTERN.search(subject))

    def parse(self, sender: str, subject: str, body_text: str, body_html: str) -> list[ParsedEmailTransaction]:
        results = []

        # Try debit
        debit_match = self.AMOUNT_DEBIT.search(body_text)
        if debit_match:
            amount = self._clean_amount(debit_match.group(1))
            if amount:
                tx_date = self._extract_date(body_text)
                desc = self._extract_description(body_text) or subject
                results.append(
                    ParsedEmailTransaction(
                        date=tx_date,
                        amount=amount,
                        tx_type="expense",
                        description=desc,
                        confidence=0.80,
                        category_hint="Others",
                    )
                )

        # Try credit
        credit_match = self.AMOUNT_CREDIT.search(body_text)
        if credit_match:
            amount = self._clean_amount(credit_match.group(1))
            if amount:
                tx_date = self._extract_date(body_text)
                desc = self._extract_description(body_text) or subject
                results.append(
                    ParsedEmailTransaction(
                        date=tx_date,
                        amount=amount,
                        tx_type="income",
                        description=desc,
                        confidence=0.80,
                        category_hint="Others",
                    )
                )

        return results

    def _extract_date(self, text: str):
        from datetime import date

        m = self.DATE_PATTERN.search(text)
        if m:
            try:
                return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
            except ValueError:
                pass
        return datetime.now().date()

    def _extract_description(self, text: str) -> Optional[str]:
        m = self.DESC_PATTERN.search(text)
        return m.group(1).strip() if m else None
