"""Generic Ollama-based fallback parser for unrecognized email sources."""

import json
import logging
import re
from datetime import date, datetime

from app.services import ollama as _ollama
from email_worker.parsers.base import BaseEmailParser, ParsedEmailTransaction

log = logging.getLogger("email_worker.parsers.generic")

_MAX_BODY_CHARS = 2000


class GenericOllamaParser(BaseEmailParser):
    def can_parse(self, sender: str, subject: str, body_text: str) -> bool:
        return True  # last resort — always returns True

    def parse(self, sender: str, subject: str, body_text: str, body_html: str) -> list[ParsedEmailTransaction]:
        if not _ollama.is_enabled():
            log.debug("Ollama disabled — generic parser skipped")
            return []

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
        if not raw:
            return []

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
        return results
