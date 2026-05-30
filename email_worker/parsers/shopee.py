"""Shopee order confirmation email parser."""

import re
from datetime import datetime

from email_worker.parsers.base import BaseEmailParser, ParsedEmailTransaction


class ShopeeParser(BaseEmailParser):
    SENDER_PATTERN = re.compile(r"shopee", re.IGNORECASE)
    SUBJECT_PATTERN = re.compile(r"đơn hàng|order|xác nhận|confirmation", re.IGNORECASE)
    AMOUNT_PATTERN = re.compile(r"(?:tổng|total|thanh toán|payment)[:\s]*([0-9.,]+)\s*(?:đ|VND|VNĐ)?", re.IGNORECASE)
    ORDER_PATTERN = re.compile(r"#?([0-9]{15,20})")

    def can_parse(self, sender: str, subject: str, body_text: str) -> bool:
        return bool(self.SENDER_PATTERN.search(sender))

    def parse(self, sender: str, subject: str, body_text: str, body_html: str) -> list[ParsedEmailTransaction]:
        m = self.AMOUNT_PATTERN.search(body_text)
        if not m:
            return []
        amount = self._clean_amount(m.group(1))
        if not amount:
            return []

        order_m = self.ORDER_PATTERN.search(body_text)
        desc = f"Shopee #{order_m.group(1)}" if order_m else "Shopee"

        return [
            ParsedEmailTransaction(
                date=datetime.now().date(),
                amount=amount,
                tx_type="expense",
                description=desc,
                confidence=0.85,
                category_hint="Shopping",
            )
        ]
