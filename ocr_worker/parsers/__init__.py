from typing import Optional

from app.models.database import ImportSource


def get_parser(source: Optional[ImportSource]):
    """Return the appropriate parser instance for *source*, or the generic fallback."""
    from ocr_worker.parsers.timo import TimoParser
    from ocr_worker.parsers.shopee import ShopeeParser
    from ocr_worker.parsers.grab import GrabParser
    from ocr_worker.parsers.generic import GenericParser

    registry = {
        ImportSource.TIMO:   TimoParser,
        ImportSource.SHOPEE: ShopeeParser,
        ImportSource.GRAB:   GrabParser,
    }
    cls = registry.get(source, GenericParser)
    return cls()
