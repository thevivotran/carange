"""
Shared utilities: row grouping, VND amount parsing, Vietnamese date parsing,
and the BaseParser ABC.
"""
import re
from abc import ABC, abstractmethod
from datetime import date, datetime
from typing import List, Optional

from ocr_worker.types import TextBlock, ParsedTransaction

# ── Amount parsing ─────────────────────────────────────────────────────────────

# Matches optional sign, then digits grouped by dots/commas (VND thousands separators)
_AMOUNT_RE = re.compile(
    r'([+\-])\s*(\d{1,3}(?:[.,]\d{3})+)'   # signed: +1.500.000
    r'|(\d{1,3}(?:[.,]\d{3})+)'             # unsigned: 1.500.000
    r'|([+\-])\s*(\d{4,})',                 # signed bare: -45000
    re.IGNORECASE,
)
_CURRENCY_STRIP_RE = re.compile(r'[₫đVND\s]', re.IGNORECASE)


def parse_vnd(text: str) -> Optional[tuple[float, str]]:
    """
    Parse a Vietnamese VND amount from *text*.
    Returns (amount_positive, tx_type) or None.
    All VND values are integers; dots/commas are always thousands separators.
    """
    cleaned = _CURRENCY_STRIP_RE.sub("", text)
    m = _AMOUNT_RE.search(cleaned)
    if not m:
        return None

    if m.group(1) is not None:           # signed long form
        sign_char, digits = m.group(1), m.group(2)
    elif m.group(3) is not None:         # unsigned long form
        sign_char, digits = "+", m.group(3)
    else:                                 # signed bare
        sign_char, digits = m.group(4), m.group(5)

    # Strip separators — all of them; VND has no decimal part
    digits = digits.replace(".", "").replace(",", "")
    try:
        value = float(digits)
    except ValueError:
        return None

    if value <= 0:
        return None

    tx_type = "expense" if sign_char == "-" else "income"
    return value, tx_type


# ── Date parsing ───────────────────────────────────────────────────────────────

_VI_MONTHS = {
    "tháng 1": 1, "tháng 2": 2, "tháng 3": 3, "tháng 4": 4,
    "tháng 5": 5, "tháng 6": 6, "tháng 7": 7, "tháng 8": 8,
    "tháng 9": 9, "tháng 10": 10, "tháng 11": 11, "tháng 12": 12,
    "th1": 1, "th2": 2, "th3": 3, "th4": 4, "th5": 5, "th6": 6,
    "th7": 7, "th8": 8, "th9": 9, "th10": 10, "th11": 11, "th12": 12,
}

_DATE_PATTERNS = [
    # DD/MM/YYYY or DD-MM-YYYY
    (re.compile(r'\b(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})\b'), "dmy"),
    # YYYY-MM-DD
    (re.compile(r'\b(\d{4})[/\-](\d{1,2})[/\-](\d{1,2})\b'), "ymd"),
    # DD/MM (no year — use current year)
    (re.compile(r'\b(\d{1,2})[/\-](\d{1,2})\b'), "dm"),
    # 15 Tháng 5 2026 / 15 tháng 5, 2026
    (re.compile(r'\b(\d{1,2})\s+tháng\s+(\d{1,2})[,\s]+(\d{4})\b', re.IGNORECASE), "dmy"),
]


def parse_date(text: str, fallback_year: Optional[int] = None) -> Optional[date]:
    t = text.lower()
    year_fb = fallback_year or date.today().year

    for pattern, fmt in _DATE_PATTERNS:
        m = pattern.search(t)
        if not m:
            continue
        try:
            if fmt == "dmy":
                d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
            elif fmt == "ymd":
                y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            else:  # "dm"
                d, mo, y = int(m.group(1)), int(m.group(2)), year_fb
            return date(y, mo, d)
        except ValueError:
            continue
    return None


# ── Row grouping ───────────────────────────────────────────────────────────────

Row = List[TextBlock]


def group_rows(blocks: List[TextBlock], gap_factor: float = 0.7) -> List[Row]:
    """
    Cluster TextBlocks into horizontal rows by Y proximity.
    *gap_factor*: blocks whose top-edges differ by less than (median_height * gap_factor)
    are considered the same row.
    """
    if not blocks:
        return []

    sorted_blocks = sorted(blocks, key=lambda b: b.y)

    heights = [b.h for b in sorted_blocks if b.h > 0]
    median_h = sorted(heights)[len(heights) // 2] if heights else 20.0
    threshold = median_h * gap_factor

    rows: List[Row] = []
    current_row: Row = [sorted_blocks[0]]
    current_y = sorted_blocks[0].y

    for block in sorted_blocks[1:]:
        if abs(block.y - current_y) <= threshold:
            current_row.append(block)
        else:
            rows.append(sorted(current_row, key=lambda b: b.x))
            current_row = [block]
            current_y = block.y

    if current_row:
        rows.append(sorted(current_row, key=lambda b: b.x))

    return rows


def row_text(row: Row) -> str:
    return " ".join(b.text for b in row)


def mean_confidence(blocks: List[TextBlock]) -> float:
    if not blocks:
        return 0.0
    return sum(b.confidence for b in blocks) / len(blocks)


# ── Base parser ────────────────────────────────────────────────────────────────

class BaseParser(ABC):
    """All source-specific parsers inherit from this."""

    @abstractmethod
    def parse(self, blocks: List[TextBlock]) -> List[ParsedTransaction]:
        """Convert raw OCR blocks into structured transactions."""

    # Helpers available to subclasses
    parse_vnd = staticmethod(parse_vnd)
    parse_date = staticmethod(parse_date)
    group_rows = staticmethod(group_rows)
    row_text = staticmethod(row_text)
    mean_confidence = staticmethod(mean_confidence)
