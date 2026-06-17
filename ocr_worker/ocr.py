"""
PaddleOCR wrapper — lazy singleton, GPU auto-detect, Vietnamese language model.
"""

import logging
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
    device = "gpu:0" if use_gpu else "cpu"
    log.info("Initialising PaddleOCR 3.x (device=%s, lang=vi, PP-OCRv5)", device)
    return PaddleOCR(
        lang="vi",
        ocr_version="PP-OCRv5",
        use_angle_cls=True,
        device=device,
    )


def _get_engine():
    global _engine
    if _engine is None:
        _engine = _init_engine()
    return _engine


def extract_blocks(image_path: str) -> List[TextBlock]:
    """Run OCR on *image_path* and return a flat list of TextBlock objects."""
    engine = _get_engine()
    result = engine.predict(image_path)

    blocks: List[TextBlock] = []
    if not result or result[0] is None:
        return blocks

    page = result[0]
    texts = page.get("rec_texts", [])
    scores = page.get("rec_scores", [])
    polys = page.get("dt_polys", [])

    for text, conf, poly in zip(texts, scores, polys):
        xs = poly[:, 0]
        ys = poly[:, 1]
        x, y = float(xs.min()), float(ys.min())
        w, h = float(xs.max() - x), float(ys.max() - y)
        blocks.append(TextBlock(text=text.strip(), confidence=conf, x=x, y=y, w=w, h=h))

    return blocks
