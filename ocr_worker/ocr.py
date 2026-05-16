"""
PaddleOCR wrapper — lazy singleton, GPU auto-detect, Vietnamese language model.
"""
import logging
import os
from typing import List

from ocr_worker.types import TextBlock

log = logging.getLogger("ocr_worker.ocr")

_engine = None


def _has_gpu() -> bool:
    try:
        import paddle  # type: ignore
        return paddle.device.get_device().startswith("gpu")
    except Exception:
        return False


def _init_engine():
    from paddleocr import PaddleOCR  # type: ignore

    use_gpu = _has_gpu()
    log.info("Initialising PaddleOCR (GPU=%s, lang=vi)", use_gpu)
    return PaddleOCR(
        use_angle_cls=True,
        lang="vi",
        use_gpu=use_gpu,
        show_log=False,
    )


def _get_engine():
    global _engine
    if _engine is None:
        _engine = _init_engine()
    return _engine


def extract_blocks(image_path: str) -> List[TextBlock]:
    """Run OCR on *image_path* and return a flat list of TextBlock objects."""
    engine = _get_engine()
    result = engine.ocr(image_path, cls=True)

    blocks: List[TextBlock] = []
    # result is list-of-pages; we always have a single image → result[0]
    page = result[0] if result else []
    if page is None:
        return blocks

    for item in page:
        bbox, (text, conf) = item
        # bbox: [[x1,y1],[x2,y2],[x3,y3],[x4,y4]] (four corners, may be rotated)
        xs = [p[0] for p in bbox]
        ys = [p[1] for p in bbox]
        x = min(xs)
        y = min(ys)
        w = max(xs) - x
        h = max(ys) - y
        blocks.append(TextBlock(text=text.strip(), confidence=conf, x=x, y=y, w=w, h=h))

    return blocks
