"""
UOB Vietnam transaction history parser — stub.

UOB statement layout not yet characterised from real screenshots.
Falls back to the generic parser until a UOB screenshot is available for analysis.
"""
from typing import List
from ocr_worker.parsers.base import BaseParser
from ocr_worker.parsers.generic import GenericParser
from ocr_worker.types import TextBlock, ParsedTransaction


class UOBParser(BaseParser):
    def parse(self, blocks: List[TextBlock]) -> List[ParsedTransaction]:
        return GenericParser().parse(blocks)
