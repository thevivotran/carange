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
    ImportSource.SHOPEE: [
        (r"\bshopee\b", 2.0),
        (r"shopee pay", 2.0),
        (r"spaylater", 1.5),
        (r"đơn hàng shopee", 1.5),
        (r"shopee mall", 1.5),
        (r"shopee siêu thị", 1.5),
        (r"don\s+da\s+mua", 2.0),      # OCR of "Đơn đã mua" — Shopee My Orders page
        (r"đơn\s+đã\s+mua", 2.0),
        (r"hoan\s+thanh.*tong.*tien", 1.5),  # order status + total — very Shopee-specific
    ],
    ImportSource.GRAB: [
        (r"\bgrab\b", 1.0),          # "grab" alone is lower weight (could be ambiguous)
        (r"grabfood", 2.0),
        (r"grabcar", 2.0),
        (r"grabbike", 2.0),
        (r"grabpay", 2.0),
        (r"grab express", 1.5),
        (r"grabcoins", 2.0),         # Activity History screenshot — "+N GrabCoins" per transaction
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
