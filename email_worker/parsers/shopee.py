"""Shopee order confirmation email parser."""

import re
from datetime import date, datetime

from email_worker.parsers.base import BaseEmailParser, ParsedEmailTransaction

# Vietnamese month abbreviations: Th01=Jan … Th12=Dec
_VN_MONTHS = {str(i).zfill(2): i for i in range(1, 13)}
_EN_MONTHS_SHORT = {
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


class ShopeeParser(BaseEmailParser):
    SENDER_PATTERN = re.compile(r"shopee", re.IGNORECASE)

    # "Tổng thanh toán: ₫1,076,400" or "Số tiền thanh toán: ₫1,076,400"
    AMOUNT_FINAL = re.compile(
        r"(?:Tổng thanh toán|Số tiền thanh toán)[:\s]*₫\s*([0-9][0-9,.]*)",
        re.IGNORECASE,
    )
    # "Tổng tiền: ₫1,380,000" — subtotal fallback
    AMOUNT_SUBTOTAL = re.compile(r"Tổng tiền[:\s]*₫\s*([0-9][0-9,.]*)", re.IGNORECASE)

    # Alphanumeric Shopee order IDs like #260530RN11A9KR
    ORDER_PATTERN = re.compile(r"Mã đơn hàng[:\s]*#?([A-Z0-9]{6,25})", re.IGNORECASE)
    ORDER_FALLBACK = re.compile(r"#([A-Z0-9]{10,25})")

    # "1. Item name (possibly multi-line)\nMẫu mã:" or "\nSố lượng:" — plain-text format
    ITEM_PATTERN = re.compile(
        r"^\s*1\.\s+(.+?)(?=\n(?:Mẫu mã|Số lượng|Giá)\s*:)",
        re.MULTILINE | re.DOTALL | re.IGNORECASE,
    )
    # Item name on the line before "Phân loại hàng:" / "Mẫu mã:" / "Màu sắc:"
    # Handles BeautifulSoup-stripped HTML where the "1." prefix is lost
    ITEM_BEFORE_VARIANT = re.compile(
        r"([^\n]{10,100})\n[\n\s]*(?:Phân loại hàng|Mẫu mã|Màu sắc)\s*:",
        re.IGNORECASE,
    )

    # "30 Th05 2026 20:33:37"
    DATE_VN = re.compile(r"(\d{1,2})\s+Th(\d{2})\s+(\d{4})", re.IGNORECASE)
    # "30/05/2026" or "2026-05-30"
    DATE_SLASH = re.compile(r"(\d{2})[/-](\d{2})[/-](\d{4})")

    def can_parse(self, sender: str, subject: str, body_text: str) -> bool:
        return (
            bool(self.SENDER_PATTERN.search(sender))
            or bool(self.SENDER_PATTERN.search(subject))
            or "shopee" in body_text.lower()
        )

    def parse(self, sender: str, subject: str, body_text: str, body_html: str) -> list[ParsedEmailTransaction]:
        amount = self._extract_amount(body_text)
        if not amount:
            return []

        order_id = self._extract_order(body_text)
        item_name = self._extract_item_name(body_text)
        if item_name:
            desc = f"Shopee – {item_name[:60]}"
        elif order_id:
            desc = f"Shopee #{order_id}"
        else:
            desc = "Shopee"

        return [
            ParsedEmailTransaction(
                date=self._extract_date(body_text),
                amount=amount,
                tx_type="expense",
                description=desc,
                confidence=0.88,
                category_hint="Shopping",
            )
        ]

    def _extract_amount(self, text: str):
        for pattern in (self.AMOUNT_FINAL, self.AMOUNT_SUBTOTAL):
            m = pattern.search(text)
            if m:
                val = self._clean_amount(m.group(1))
                if val:
                    return val
        return None

    def _extract_order(self, text: str):
        m = self.ORDER_PATTERN.search(text) or self.ORDER_FALLBACK.search(text)
        return m.group(1) if m else None

    def _extract_item_name(self, text: str):
        for pattern in (self.ITEM_PATTERN, self.ITEM_BEFORE_VARIANT):
            m = pattern.search(text)
            if m:
                name = re.sub(r"\s+", " ", m.group(1)).strip()
                if name and len(name) > 5:
                    return name
        return None

    def _extract_date(self, text: str) -> date:
        # Vietnamese format: "30 Th05 2026"
        m = self.DATE_VN.search(text)
        if m:
            day, mon_str, year = int(m.group(1)), m.group(2), int(m.group(3))
            mon = _VN_MONTHS.get(mon_str)
            if mon:
                try:
                    return date(year, mon, day)
                except ValueError:
                    pass

        # DD/MM/YYYY fallback
        m = self.DATE_SLASH.search(text)
        if m:
            try:
                return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
            except ValueError:
                pass

        return datetime.now().date()
