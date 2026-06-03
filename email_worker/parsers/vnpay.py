"""VNPAY payment receipt email parser.

Handles "BIÊN LAI THANH TOÁN" (Payment Receipt) emails from noreply@vnpayapp.vn.
These emails arrive directly or forwarded (quoted with >) by the account holder.
The _unwrap_forwarded step in email_parser.py strips the quoting and restores
noreply@vnpayapp.vn as the effective sender before this parser is called.

Plain text layout (after quote-stripping):
  BIÊN LAI THANH TOÁN
  Ngày, giờ giao dịch:
  Trans. Date, Time
  03/06/2026 20:53
  ...
  Tóm tắt giao dịch:
  Transaction summary
  Thanh Toán dịch vụ VNPAY
  ...
  - Số tiền thanh toán:
  Payment amount
  183.000 VND
"""

import re
from datetime import date, datetime

from email_worker.parsers.base import BaseEmailParser, ParsedEmailTransaction

_IDENTIFY_RE = re.compile(r"BIÊN LAI THANH TOÁN|vnpayapp\.vn", re.IGNORECASE)

# "Payment amount\n183.000 VND"  — always the last/total amount after fees
_AMOUNT_RE = re.compile(
    r"(?:Số tiền thanh toán|Payment amount)[^\n]*\n\s*([0-9][0-9.]*)\s*VND",
    re.IGNORECASE,
)

# "03/06/2026 20:53"  DD/MM/YYYY
_DATE_RE = re.compile(r"(\d{2})/(\d{2})/(\d{4})")

# "Tóm tắt giao dịch:\nTransaction summary\n<value>"
_DESC_RE = re.compile(
    r"Tóm tắt giao dịch[^\n]*\n[^\n]*\n([^\n]+)",
    re.IGNORECASE,
)


class VNPayParser(BaseEmailParser):
    def can_parse(self, sender: str, subject: str, body_text: str) -> bool:
        return "vnpayapp.vn" in sender.lower() or bool(_IDENTIFY_RE.search(body_text))

    def parse(self, sender: str, subject: str, body_text: str, body_html: str) -> list[ParsedEmailTransaction]:
        m_amt = _AMOUNT_RE.search(body_text)
        if not m_amt:
            return []
        amount = self._clean_amount(m_amt.group(1))
        if not amount:
            return []

        tx_date = self._extract_date(body_text)
        desc = self._extract_description(body_text)

        return [
            ParsedEmailTransaction(
                date=tx_date,
                amount=amount,
                tx_type="expense",
                description=desc,
                confidence=0.92,
                category_hint=None,
                payment_method="bank_transfer",
            )
        ]

    def _extract_date(self, text: str) -> date:
        m = _DATE_RE.search(text)
        if m:
            try:
                return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
            except ValueError:
                pass
        return datetime.now().date()

    def _extract_description(self, text: str) -> str:
        m = _DESC_RE.search(text)
        if m:
            desc = m.group(1).strip()
            if desc:
                return f"VNPay – {desc}"
        return "VNPay"
