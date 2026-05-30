"""Base class for all bank/merchant email parsers."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from typing import Optional


@dataclass
class ParsedEmailTransaction:
    date: date
    amount: float
    tx_type: str  # "expense" | "income"
    description: str
    confidence: float
    category_hint: Optional[str] = None
    payment_method: str = "bank_transfer"


class BaseEmailParser(ABC):
    """Each bank/source subclass implements parse() for its email HTML/text format."""

    @abstractmethod
    def can_parse(self, sender: str, subject: str, body_text: str) -> bool:
        """Return True if this parser should handle the given email."""

    @abstractmethod
    def parse(self, sender: str, subject: str, body_text: str, body_html: str) -> list[ParsedEmailTransaction]:
        """Extract transactions from the email. Return empty list if none found."""

    # ── Shared helpers ───────────────────────────────────────────────────────

    def _clean_amount(self, raw: str) -> Optional[float]:
        """Parse Vietnamese VND amount strings like '250,000' or '1.500.000'."""
        import re

        cleaned = re.sub(r"[^\d]", "", raw)
        if not cleaned:
            return None
        try:
            return float(cleaned)
        except ValueError:
            return None
