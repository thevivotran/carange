from typing import Optional

from app.models.database import ImportSource


def get_parser(source: Optional[ImportSource]):
    """Return the appropriate parser instance for *source*, or the generic fallback."""
    from ocr_worker.parsers.timo import TimoParser
    from ocr_worker.parsers.shopee import ShopeeParser
    from ocr_worker.parsers.grab import GrabParser
    from ocr_worker.parsers.vpbank import VPBankParser
    from ocr_worker.parsers.techcombank import TechcombankParser
    from ocr_worker.parsers.mbbank import MBBankParser
    from ocr_worker.parsers.vietcombank import VietcomBankParser
    from ocr_worker.parsers.generic import GenericParser

    registry = {
        ImportSource.TIMO: TimoParser,
        ImportSource.SHOPEE: ShopeeParser,
        ImportSource.GRAB: GrabParser,
        ImportSource.VPBANK: VPBankParser,
        ImportSource.TECHCOMBANK: TechcombankParser,
        ImportSource.MBBANK: MBBankParser,
        ImportSource.VIETCOMBANK: VietcomBankParser,
    }
    cls = registry.get(source, GenericParser)
    return cls()
