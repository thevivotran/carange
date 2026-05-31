"""Payoo payment gateway confirmation email parser."""

import re
from datetime import date, datetime

from email_worker.parsers.base import BaseEmailParser, ParsedEmailTransaction


class PayooParser(BaseEmailParser):
    SENDER_PATTERN = re.compile(r"payoo", re.IGNORECASE)

    # "Tổng thanh toán (VND) 39.200"
    AMOUNT_VN = re.compile(r"Tổng thanh toán\s*\(VND\)\s+([0-9][0-9.]*)", re.IGNORECASE)
    # "Total amount (VND) 39.200"
    AMOUNT_EN = re.compile(r"Total amount\s*\(VND\)\s+([0-9][0-9.]*)", re.IGNORECASE)
    # "amount *39.200* VND" or "số tiền *39.200* VND"
    AMOUNT_INLINE = re.compile(r"(?:amount|số tiền)\s+\*?([0-9][0-9.]*)\*?\s+VND", re.IGNORECASE)

    # "25/05/2026 08:38:34"
    DATE_PATTERN = re.compile(r"(\d{2})/(\d{2})/(\d{4})")

    # "đơn hàng 1228000188593" or "order 1228000188593"
    ORDER_PATTERN = re.compile(r"(?:đơn hàng|order)\s+\*?(\d{8,20})\*?", re.IGNORECASE)

    def can_parse(self, sender: str, subject: str, body_text: str) -> bool:
        return (
            bool(self.SENDER_PATTERN.search(sender))
            or "payoo" in body_text.lower()
            or "payoo.com.vn" in body_text.lower()
        )

    def parse(self, sender: str, subject: str, body_text: str, body_html: str) -> list[ParsedEmailTransaction]:
        amount = self._extract_amount(body_text)
        if not amount:
            return []

        order_m = self.ORDER_PATTERN.search(body_text)
        order_id = order_m.group(1) if order_m else None
        desc = f"Payoo #{order_id}" if order_id else "Payoo"

        return [
            ParsedEmailTransaction(
                date=self._extract_date(body_text),
                amount=amount,
                tx_type="expense",
                description=desc,
                confidence=0.88,
                category_hint="Chi phí khác",
                payment_method="credit_card",
            )
        ]

    def _extract_amount(self, text: str):
        for pat in (self.AMOUNT_VN, self.AMOUNT_EN, self.AMOUNT_INLINE):
            m = pat.search(text)
            if m:
                val = self._clean_amount(m.group(1))
                if val:
                    return val
        return None

    def _extract_date(self, text: str) -> date:
        m = self.DATE_PATTERN.search(text)
        if m:
            try:
                return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
            except ValueError:
                pass
        return datetime.now().date()
