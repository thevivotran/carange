import ast
import logging
import math
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone

from sqlalchemy.orm import Session

from app.models.database import LearnedParser
from ocr_worker.types import ParsedTransaction, TextBlock

log = logging.getLogger("ocr_worker.learned_parser_store")

# AST node types and attribute names that can escape the restricted namespace.
_BANNED_NODE_TYPES = (ast.Import, ast.ImportFrom)
_BANNED_ATTR_NAMES = frozenset(
    {
        "__class__",
        "__mro__",
        "__subclasses__",
        "__globals__",
        "__builtins__",
        "__import__",
        "__reduce__",
        "__reduce_ex__",
    }
)
_BANNED_CALL_NAMES = frozenset(
    {"getattr", "setattr", "globals", "locals", "compile", "eval", "exec", "open", "__import__"}
)


def _ast_is_safe(script: str) -> bool:
    """Return False if the script contains any AST node that can escape the sandbox."""
    try:
        tree = ast.parse(script)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if isinstance(node, _BANNED_NODE_TYPES):
            return False
        if isinstance(node, ast.Attribute) and node.attr in _BANNED_ATTR_NAMES:
            return False
        if isinstance(node, ast.Name) and node.id in _BANNED_CALL_NAMES:
            return False
    return True


def lookup(db: Session, full_text: str) -> "LearnedParser | None":
    parsers = db.query(LearnedParser).order_by(LearnedParser.hit_count.desc()).all()
    for lp in parsers:
        keywords = lp.detection_keywords or []
        if any(kw.lower() in full_text for kw in keywords):
            lp.hit_count = (lp.hit_count or 0) + 1
            lp.last_used_at = datetime.now(timezone.utc)
            db.flush()
            return lp
    return None


def save(db: Session, source_name: str, keywords: list[str], script: str) -> "LearnedParser":
    existing = db.query(LearnedParser).filter(LearnedParser.source_name == source_name).first()
    if existing is not None:
        existing.detection_keywords = keywords
        existing.extraction_script = script
        db.flush()
        return existing
    lp = LearnedParser(
        source_name=source_name,
        detection_keywords=keywords,
        extraction_script=script,
    )
    db.add(lp)
    db.flush()
    return lp


def run_parser(script: str, blocks: list[TextBlock]) -> "list[ParsedTransaction] | None":
    if not _ast_is_safe(script):
        log.warning("run_parser: script rejected by AST safety check")
        return None
    try:
        namespace: dict = {
            "re": re,
            "math": math,
            "date": date,
            "datetime": datetime,
            "dataclass": dataclass,
            "TextBlock": TextBlock,
            "ParsedTransaction": ParsedTransaction,
            "__builtins__": {},
        }
        exec(script, namespace)  # noqa: S102
        result = namespace["parse"](blocks)
        if isinstance(result, list) and len(result) > 0:
            return result
        return None
    except Exception as exc:
        log.debug("run_parser failed: %s", exc)
        return None
