"""
Keyword-based source detection from OCR text.

Scans all extracted text blocks for identifying strings. Returns the most
confidently matched ImportSource, or None if no source is recognised.
"""
import re
from typing import List, Optional

from app.models.database import ImportSource
from ocr_worker.types import TextBlock

# Each source maps to (keywords, weight) pairs.
# Weight accumulates; highest total wins.
_RULES: dict[ImportSource, List[tuple[str, float]]] = {
    ImportSource.TIMO: [
        (r"\btimo\b", 2.0),
        (r"timo\.vn", 2.0),
        (r"ví timo", 1.5),
    ],
    ImportSource.UOB: [
        (r"\buob\b", 2.0),
        (r"united overseas", 2.0),
        (r"uob personal", 1.5),
        (r"uob bank", 1.5),
    ],
    ImportSource.LIOBANK: [
        (r"liobank", 2.0),
        (r"lio bank", 2.0),
        (r"\blio\b", 0.8),
    ],
    ImportSource.SHOPEE: [
        (r"\bshopee\b", 2.0),
        (r"shopee pay", 2.0),
        (r"spaylater", 1.5),
        (r"đơn hàng shopee", 1.5),
        (r"shopee mall", 1.5),
        (r"shopee siêu thị", 1.5),
    ],
    ImportSource.GRAB: [
        (r"\bgrab\b", 1.0),          # "grab" alone is lower weight (could be ambiguous)
        (r"grabfood", 2.0),
        (r"grabcar", 2.0),
        (r"grabbike", 2.0),
        (r"grabpay", 2.0),
        (r"grab express", 1.5),
    ],
}

_MIN_CONFIDENCE = 1.5   # minimum accumulated weight to declare a match


def detect_source(blocks: List[TextBlock]) -> Optional[ImportSource]:
    """Return the best-matched ImportSource or None."""
    full_text = " ".join(b.text.lower() for b in blocks)

    scores: dict[ImportSource, float] = {}
    for source, rules in _RULES.items():
        total = 0.0
        for pattern, weight in rules:
            if re.search(pattern, full_text):
                total += weight
        if total >= _MIN_CONFIDENCE:
            scores[source] = total

    if not scores:
        return None
    return max(scores, key=lambda s: scores[s])
