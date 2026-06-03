"""Grab e-receipt email parser — Food, Transport, and Express.

Processes receipts from personal and family Grab accounts (PERSONAL,
CÁ NHÂN, FAMILY, GIA ĐÌNH profiles). Family-account rides are treated
as household expenses — the account holder pays for all rides regardless
of which profile name appears on the receipt.

Category mapping:
  Food    → Ăn uống
  Car/Bike → Di chuyển
  Express → Chi phí khác
"""

import re
from datetime import date, datetime

from bs4 import BeautifulSoup

from email_worker.parsers.base import BaseEmailParser, ParsedEmailTransaction

_EN_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}

# ─── Profile detection ────────────────────────────────────────────────────────
# Matches Profile / Hồ sơ line followed by an accepted profile value.
# Accepted: PERSONAL, Cá nhân, FAMILY, Gia đình
_PERSONAL_RE = re.compile(
    r"(?:Profile|Hồ\s*sơ)\s*\n\s*(?:PERSONAL|CÁ\s*NHÂN|Cá\s*nhân|FAMILY|GIA\s*ĐÌNH|Gia\s*đình)",
    re.IGNORECASE,
)

# ─── Service-type signals ─────────────────────────────────────────────────────
_FOOD_RE = re.compile(r"Chúc bạn ngon miệng|BẠN TRẢ|Đặt từ", re.IGNORECASE)
_EXPRESS_RE = re.compile(r"\bExpress\b|\bGrabExpress\b", re.IGNORECASE)
_TRANSPORT_RE = re.compile(
    r"Hope you enjoyed your ride|Hy vọng bạn đã có một chuyến đi",
    re.IGNORECASE,
)

# Extracts the service-type label printed at the top of transport receipts.
_SERVICE_TYPE_RE = re.compile(
    r"^(Car(?:\s+Plus)?(?:\s+Xe\s+Điện)?|Bike(?:\s+Plus)?|GrabBike|GrabCar|Taxi|Express|GrabExpress)\s*$",
    re.MULTILINE | re.IGNORECASE,
)

# ─── Amount patterns ──────────────────────────────────────────────────────────
# Food: "BẠN TRẢ       218200₫"
_FOOD_AMT_PAY = re.compile(r"BẠN\s+TRẢ\s+([0-9][0-9,.]*)\s*(?:₫|đ|VND|VNĐ)", re.IGNORECASE)
# Food fallback: "Tổng cộng\n218200₫"
_FOOD_AMT_TOTAL = re.compile(
    r"Tổng cộng\s*\n\s*([0-9][0-9,.]*)\s*(?:₫|đ|VND|VNĐ)",
    re.IGNORECASE | re.MULTILINE,
)
# Transport EN: "Total Paid\nVND 70.000"  or inline "VND 70.000"
_TRANSPORT_AMT_EN = re.compile(
    r"(?:Total Paid)\s*\n\s*VND\s+([0-9][0-9.]*)|VND\s+([0-9][0-9.]*)",
    re.IGNORECASE,
)
# Transport VN: "Tổng đã thanh toán VND 69.000"
_TRANSPORT_AMT_VN = re.compile(r"Tổng đã thanh toán\s+VND\s+([0-9][0-9.]*)", re.IGNORECASE)

# ─── Date patterns ────────────────────────────────────────────────────────────
# Food / short: "17 May 26 18:19" or "30 May 26 18:26"
_DATE_SHORT = re.compile(
    r"(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{2,4})",
    re.IGNORECASE,
)
# Transport EN: "Picked up on 14 March 2026"
_DATE_PICKUP_EN = re.compile(r"Picked up on\s+(\d{1,2})\s+(\w+)\s+(\d{4})", re.IGNORECASE)
# Transport VN: "Ngày đi 25 May 2026"
_DATE_PICKUP_VN = re.compile(r"Ngày đi\s+(\d{1,2})\s+(\w+)\s+(\d{4})", re.IGNORECASE)

# ─── Description helpers ──────────────────────────────────────────────────────
_RESTAURANT_RE = re.compile(r"Đặt từ[ \t\n]*(.+?)(?:\n|$)", re.IGNORECASE | re.MULTILINE)


class GrabParser(BaseEmailParser):
    def can_parse(self, sender: str, subject: str, body_text: str) -> bool:
        return (
            bool(re.search(r"grab", sender, re.IGNORECASE))
            or bool(re.search(r"grab", subject, re.IGNORECASE))
            or "grab.com" in body_text.lower()
            or bool(_FOOD_RE.search(body_text))
        )

    def parse(self, sender: str, subject: str, body_text: str, body_html: str) -> list[ParsedEmailTransaction]:
        if not _PERSONAL_RE.search(body_text):
            return []

        if _FOOD_RE.search(body_text):
            return self._parse_food(body_text)
        if _EXPRESS_RE.search(body_text):
            return self._parse_express(body_text)
        if _TRANSPORT_RE.search(body_text):
            return self._parse_transport(body_text, body_html)
        return []

    # ── Food ──────────────────────────────────────────────────────────────────

    def _parse_food(self, text: str) -> list[ParsedEmailTransaction]:
        amount = None
        for pat in (_FOOD_AMT_PAY, _FOOD_AMT_TOTAL):
            m = pat.search(text)
            if m:
                amount = self._clean_amount(m.group(1))
                if amount:
                    break
        if not amount:
            return []

        restaurant = self._extract_restaurant(text)
        desc = f"Grab Food – {restaurant}" if restaurant else "Grab Food"

        return [
            ParsedEmailTransaction(
                date=self._extract_date_short(text),
                amount=amount,
                tx_type="expense",
                description=desc,
                confidence=0.90,
                category_hint="Ăn uống",
            )
        ]

    # ── Transport ─────────────────────────────────────────────────────────────

    def _parse_transport(self, text: str, html: str = "") -> list[ParsedEmailTransaction]:
        amount = None
        for pat in (_TRANSPORT_AMT_VN, _TRANSPORT_AMT_EN):
            m = pat.search(text)
            if m:
                raw = m.group(1) or (m.lastindex >= 2 and m.group(2))
                amount = self._clean_amount(raw) if raw else None
                if amount:
                    break
        if not amount:
            return []

        service_type = self._extract_service_type(text)
        label = f"Grab {service_type}" if service_type else "Grab Transport"

        pickup, dropoff = self._extract_route(html)
        desc = f'{label}: "{pickup}" - "{dropoff}"' if pickup and dropoff else label

        return [
            ParsedEmailTransaction(
                date=self._extract_date_transport(text),
                amount=amount,
                tx_type="expense",
                description=desc,
                confidence=0.90,
                category_hint="Di chuyển",
            )
        ]

    # ── Express ───────────────────────────────────────────────────────────────

    def _parse_express(self, text: str) -> list[ParsedEmailTransaction]:
        # Express receipts share the same amount layout as transport
        amount = None
        for pat in (_TRANSPORT_AMT_VN, _TRANSPORT_AMT_EN, _FOOD_AMT_PAY):
            m = pat.search(text)
            if m:
                raw = m.group(1) or (m.lastindex >= 2 and m.group(2))
                amount = self._clean_amount(raw) if raw else None
                if amount:
                    break
        if not amount:
            return []

        return [
            ParsedEmailTransaction(
                date=self._extract_date_transport(text) or self._extract_date_short(text),
                amount=amount,
                tx_type="expense",
                description="Grab Express",
                confidence=0.88,
                category_hint="Chi phí khác",
            )
        ]

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _extract_route(self, html: str) -> tuple[str, str]:
        """Return (pickup_address, dropoff_address) from Grab ride receipt HTML.

        Grab receipts render pickup/dropoff inside a table where each row has:
          <td> <img alt="pick-up"|"drop-off"> </td>
          <td> <div>Address text</div> <div>Time</div> </td>
        """
        if not html:
            return "", ""
        try:
            soup = BeautifulSoup(html, "lxml")

            def _addr(alt: str) -> str:
                img = soup.find("img", {"alt": alt})
                if not img:
                    return ""
                img_td = img.find_parent("td")
                if not img_td:
                    return ""
                addr_td = img_td.find_next_sibling("td")
                if not addr_td:
                    return ""
                first_div = addr_td.find("div")
                return first_div.get_text().strip() if first_div else ""

            return _addr("pick-up"), _addr("drop-off")
        except Exception:
            return "", ""

    def _extract_restaurant(self, text: str):
        m = _RESTAURANT_RE.search(text)
        if m:
            name = m.group(1).strip()
            return name or None
        return None

    def _extract_service_type(self, text: str):
        m = _SERVICE_TYPE_RE.search(text)
        return m.group(1).strip() if m else None

    @staticmethod
    def _parse_date_parts(day_str: str, mon_str: str, yr_str: str) -> date | None:
        """Convert matched day/month-name/year strings to a date, or None on failure."""
        mon = _EN_MONTHS.get(mon_str.lower(), 0)
        if not mon:
            return None
        yr_raw = int(yr_str)
        year = 2000 + yr_raw if yr_raw < 100 else yr_raw
        try:
            return date(year, mon, int(day_str))
        except ValueError:
            return None

    def _extract_date_short(self, text: str) -> date:
        m = _DATE_SHORT.search(text)
        if m:
            d = self._parse_date_parts(m.group(1), m.group(2), m.group(3))
            if d:
                return d
        return datetime.now().date()

    def _extract_date_transport(self, text: str) -> date:
        for pat in (_DATE_PICKUP_VN, _DATE_PICKUP_EN, _DATE_SHORT):
            m = pat.search(text)
            if m:
                d = self._parse_date_parts(m.group(1), m.group(2), m.group(3))
                if d:
                    return d
        return datetime.now().date()
