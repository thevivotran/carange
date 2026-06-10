"""Generic Ollama-based fallback parser for unrecognized email sources.

When this parser successfully extracts transactions, it fires a second LLM call
to generate Python regex patterns for the sender's email format.  Those patterns
are stored in learned_patterns.json and applied by LearnedRegexParser on all
subsequent emails from the same domain — bypassing the slow LLM call entirely.
"""

import json
import logging
import re
import threading
from datetime import date, datetime

from app.services import ollama as _ollama
from email_worker.parsers.base import BaseEmailParser, LLMUnavailableError, ParsedEmailTransaction

log = logging.getLogger("email_worker.parsers.generic")

_MAX_BODY_CHARS = 2000
_MAX_BODY_PATTERN_GEN = 1500  # shorter context for pattern generation


class GenericOllamaParser(BaseEmailParser):
    def can_parse(self, sender: str, subject: str, body_text: str) -> bool:
        return True  # last resort — always returns True

    def parse(self, sender: str, subject: str, body_text: str, body_html: str) -> list[ParsedEmailTransaction]:
        if not _ollama.is_enabled():
            raise LLMUnavailableError("LLM fallback disabled (OLLAMA_URL not set)")

        today = datetime.now().date().isoformat()
        truncated = body_text[:_MAX_BODY_CHARS]
        prompt = (
            f"Today: {today}\n"
            f"Email from: {sender}\n"
            f"Subject: {subject}\n\n"
            f"Email body:\n{truncated}\n\n"
            "Extract financial transactions from this email.\n"
            "Return a JSON array. Each element: "
            '{"date":"YYYY-MM-DD","amount":number,"type":"expense"|"income","description":"...","category_hint":"..."}\n'
            "If no transaction found return []. Return ONLY the JSON array."
        )
        raw = _ollama.generate_sync(
            prompt=prompt,
            system=(
                "You extract financial transactions from Vietnamese bank and merchant emails. "
                "Return only valid JSON. Never add text outside the array."
            ),
        )
        if raw is None:
            # generate_sync returns None only when vLLM is unreachable/errored —
            # an answered prompt with no transactions yields "[]" instead.
            raise LLMUnavailableError(f"vLLM at {_ollama.OLLAMA_URL} returned no response")

        m = re.search(r"\[.*?\]", raw, re.DOTALL)
        if not m:
            return []

        try:
            items = json.loads(m.group())
        except json.JSONDecodeError:
            return []

        results = []
        for item in items:
            try:
                results.append(
                    ParsedEmailTransaction(
                        date=date.fromisoformat(item["date"]),
                        amount=float(item["amount"]),
                        tx_type=str(item["type"]).lower(),
                        description=str(item.get("description", subject)),
                        confidence=0.65,  # LLM fallback = lower confidence → needs_review
                        category_hint=item.get("category_hint"),
                    )
                )
            except (KeyError, ValueError, TypeError) as exc:
                log.debug("Skipping malformed item %s: %s", item, exc)

        if results:
            # Fire pattern generation in a background thread so it doesn't delay
            # the email processing pipeline.
            threading.Thread(
                target=_generate_and_store_patterns,
                args=(sender, subject, body_text, results),
                daemon=True,
            ).start()

        return results


def _generate_and_store_patterns(
    sender: str,
    subject: str,
    body_text: str,
    extracted: list[ParsedEmailTransaction],
) -> None:
    """Ask the LLM to produce regex patterns for this sender and persist them."""
    from email_worker.learned_patterns import get_patterns, save_patterns

    # Skip if we already have patterns for this sender (avoid overwriting good ones)
    if get_patterns(sender) is not None:
        return

    sample = json.dumps(
        [
            {
                "date": str(t.date),
                "amount": t.amount,
                "type": t.tx_type,
                "description": t.description,
            }
            for t in extracted
        ],
        ensure_ascii=False,
    )

    truncated_body = body_text[:_MAX_BODY_PATTERN_GEN]
    prompt = (
        f"Email from: {sender}\n"
        f"Subject: {subject}\n\n"
        f"Email body:\n{truncated_body}\n\n"
        f"Transactions already extracted from this email:\n{sample}\n\n"
        "Generate Python regex patterns to extract the SAME data from future emails "
        "with this format. The patterns will be used with re.search(pattern, body_text, re.IGNORECASE).\n\n"
        "Return ONLY a JSON object with this exact structure:\n"
        "{\n"
        '  "amount_patterns": [\n'
        '    {"pattern": "...", "group": 1, "tx_type": "expense"|"income"|"detect"}\n'
        "  ],\n"
        '  "date_pattern": {"pattern": "...", "group": 1, "format": "%d/%m/%Y"},\n'
        '  "desc_pattern": {"pattern": "...", "group": 1},\n'
        '  "type_detect": {"income_keywords": ["ghi có", ...], "expense_keywords": ["ghi nợ", ...]}\n'
        "}\n"
        "Rules:\n"
        "- Use capturing groups () for the values you want to extract\n"
        "- Amount patterns: capture only digits and separators like 1,234,567 or 1.234.567\n"
        "- Use tx_type=detect when the email can contain both income and expense\n"
        "- Return ONLY the JSON object, no extra text"
    )

    raw = _ollama.generate_sync(
        prompt=prompt,
        system=(
            "You generate Python regex patterns for financial email parsing. "
            "Return only valid JSON. Patterns must be valid Python regex syntax."
        ),
    )
    if not raw:
        log.debug("Pattern generation: no LLM response for sender %s", sender)
        return

    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        log.debug("Pattern generation: no JSON object found in response for sender %s", sender)
        return

    try:
        patterns = json.loads(m.group())
    except json.JSONDecodeError as exc:
        log.debug("Pattern generation: JSON parse error for sender %s: %s", sender, exc)
        return

    # Validate that the generated patterns actually compile and match the source email
    validated = _validate_patterns(patterns, body_text, extracted)
    if not validated:
        log.info("Pattern generation: patterns for %s failed validation — not saving", sender)
        return

    save_patterns(sender, validated)
    log.info("Learned new regex patterns for sender domain from %s", sender)


def _validate_patterns(patterns: dict, body_text: str, extracted: list[ParsedEmailTransaction]) -> dict | None:
    """Verify generated patterns compile and match at least one transaction amount."""
    validated = {}

    # Validate amount patterns — keep only those that compile and match something
    valid_amount_patterns = []
    for ap in patterns.get("amount_patterns", []):
        try:
            m = re.search(ap["pattern"], body_text, re.IGNORECASE)
            if m:
                raw = m.group(ap.get("group", 1))
                # Check it looks like a number
                if re.search(r"\d", raw):
                    valid_amount_patterns.append(ap)
        except re.error as exc:
            log.debug("Invalid regex pattern %r: %s", ap.get("pattern"), exc)

    if not valid_amount_patterns:
        return None

    validated["amount_patterns"] = valid_amount_patterns

    # Validate date pattern
    dp = patterns.get("date_pattern")
    if dp:
        try:
            re.compile(dp["pattern"])
            validated["date_pattern"] = dp
        except re.error:
            pass

    # Validate desc pattern
    dsp = patterns.get("desc_pattern")
    if dsp:
        try:
            re.compile(dsp["pattern"])
            validated["desc_pattern"] = dsp
        except re.error:
            pass

    if "type_detect" in patterns:
        validated["type_detect"] = patterns["type_detect"]

    return validated
