import re
from datetime import date

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.models.database import InsightType, get_db
from app.routers.fragments._helpers import render_fragment
from app.services import ollama as _ollama
from app.services.budget_service import compute_budget_rows
from app.services.fiscal_period import current_period_label, get_month_start_day
from app.services.insight_service import get_insight

router = APIRouter()

# Weekly digest is emitted as three labelled lines (SUMMARY/NOTABLE/RECOMMENDATION).
# Map each to a friendly label, icon and colour tone so the template can render
# one box per section instead of a single wall of text.
_DIGEST_SECTIONS = {
    "SUMMARY": ("Summary", "fa-clipboard-list", "blue"),
    "NOTABLE": ("Notable", "fa-lightbulb", "amber"),
    "RECOMMENDATION": ("Recommendation", "fa-bullseye", "green"),
}


def _parse_digest(text: str | None) -> list[dict]:
    """Split a SUMMARY/NOTABLE/RECOMMENDATION digest into labelled sections.

    Returns an empty list if the text is missing or doesn't follow the
    expected header format, so the caller can fall back to raw text.
    """
    if not text:
        return []
    sections: list[dict] = []
    current: dict | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = re.match(r"^([A-Z][A-Z ]+):\s*(.*)$", line)
        key = match.group(1).strip() if match else None
        if match and key in _DIGEST_SECTIONS:
            label, icon, tone = _DIGEST_SECTIONS[key]
            current = {"label": label, "icon": icon, "tone": tone, "text": match.group(2).strip()}
            sections.append(current)
        elif current:
            current["text"] = f"{current['text']} {line}".strip()
    return [s for s in sections if s["text"]]


def _split_sentences(text: str | None) -> list[str]:
    """Break free-form advisor prose into individual sentences for boxing."""
    if not text:
        return []
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]


@router.get("/digest")
async def fragment_pulse_digest(request: Request, db: Session = Depends(get_db)):
    insight = get_insight(db, InsightType.WEEKLY_DIGEST)
    return render_fragment(
        request,
        "partials/pulse/_digest.html",
        {
            "digest_text": insight.content if insight else None,
            "digest_sections": _parse_digest(insight.content if insight else None),
            "generated_at": insight.generated_at if insight else None,
            "ollama_enabled": _ollama.is_enabled(),
        },
    )


@router.get("/budget-advisor")
async def fragment_pulse_budget_advisor(request: Request, db: Session = Depends(get_db)):
    today = date.today()
    day = get_month_start_day(db)
    year_month = current_period_label(today, day)

    rows = compute_budget_rows(db, year_month, day)
    insight = get_insight(db, InsightType.BUDGET_ADVISOR)

    return render_fragment(
        request,
        "partials/pulse/_budget_advisor.html",
        {
            "advisor_text": insight.content if insight else None,
            "advisor_sentences": _split_sentences(insight.content if insight else None),
            "generated_at": insight.generated_at if insight else None,
            "has_budget": bool(rows),
            "ollama_enabled": _ollama.is_enabled(),
            # Mirror the Budget page definitions so the counts match what the
            # user sees there: "over budget" = negative available balance
            # (cumulative, incl. rollover); "at risk" = amber threshold (>=75%).
            "over_count": len([r for r in rows if r["available_balance"] < 0]),
            "at_risk_count": len([r for r in rows if r["available_balance"] >= 0 and r["cumulative_pct"] >= 75]),
        },
    )
