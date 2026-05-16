from dataclasses import dataclass
from datetime import date
from typing import Optional


@dataclass
class TextBlock:
    text: str
    confidence: float
    x: float  # left edge (pixels)
    y: float  # top edge  (pixels)
    w: float  # width
    h: float  # height

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2

    @property
    def right(self) -> float:
        return self.x + self.w

    @property
    def bottom(self) -> float:
        return self.y + self.h


@dataclass
class ParsedTransaction:
    date: date
    amount: float  # always positive
    tx_type: str  # "income" | "expense"
    description: str
    confidence: float  # 0.0 – 1.0
    raw_text: str = ""
    category_hint: Optional[str] = None  # suggested category name for lookup
