from typing import List, Optional

from app.models.database import ImportSource
from ocr_worker.types import TextBlock, ParsedTransaction


def get_parser(source: Optional[ImportSource]):
    """Return the appropriate parser instance for *source*, or the generic fallback."""
    from ocr_worker.parsers.timo import TimoParser
    from ocr_worker.parsers.uob import UOBParser
    from ocr_worker.parsers.liobank import LioBankParser
    from ocr_worker.parsers.shopee import ShopeeParser
    from ocr_worker.parsers.grab import GrabParser
    from ocr_worker.parsers.generic import GenericParser

    registry = {
        ImportSource.TIMO:    TimoParser,
        ImportSource.UOB:     UOBParser,
        ImportSource.LIOBANK: LioBankParser,
        ImportSource.SHOPEE:  ShopeeParser,
        ImportSource.GRAB:    GrabParser,
    }
    cls = registry.get(source, GenericParser)
    return cls()
