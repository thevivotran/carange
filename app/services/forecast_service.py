"""Cash-flow forecast service — projects running balance over recurring
templates, pending project payments, and maturing savings bundles.

Pure read-only function: no writes, no commits.
"""

from datetime import date, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.models.database import (
    FinancialProject,
    PaymentStatus,
    ProjectPayment,
    SavingsBundle,
    SavingsStatus,
    TransactionTemplate,
    TransactionType,
)
from app.services.budget_service import compute_budget_rows
from app.services.cadence import iter_occurrences
from app.services.currency_format import get_current_currency
from app.services.dashboard_service import get_cash_on_hand
from app.services.fiscal_period import current_period_label, fiscal_window, get_month_start_day
from app.services.settings_service import get_setting


def build_forecast(db: Session, horizon_days: int = 90, include_budget_estimate: bool = True) -> dict[str, Any]:
    """Build a running-balance cash-flow forecast over the given horizon.

    When ``include_budget_estimate`` is True, the forecast also includes
    estimated discretionary outflow events (source="budget_estimate",
    estimated=True) representing the remaining unspent budget headroom for
    the current fiscal period. The leftover headroom for each category is
    smeared evenly across the remaining days of the period that fall within
    the forecast window, emitting one estimated event per applicable day.
    """
    start_balance = get_cash_on_hand(db)
    currency = get_current_currency(db)
    buffer = float(get_setting(db, "forecast_buffer", "0") or 0)

    start = date.today()
    end = start + timedelta(days=horizon_days)

    events: list[dict[str, Any]] = []

    # ── Recurring templates ──────────────────────────────────────────────────
    templates = (
        db.query(TransactionTemplate)
        .filter(
            TransactionTemplate.is_active.is_(True),
            TransactionTemplate.cadence.isnot(None),
            TransactionTemplate.next_run_at.isnot(None),
        )
        .all()
    )
    # Tracks, per category, the total magnitude of EXPENSE template events
    # already scheduled within the forecast window — used below to avoid
    # double-counting against estimated budget-headroom events.
    scheduled_expense_by_category: dict[int, float] = {}
    for tmpl in templates:
        amount = float(tmpl.amount)
        first = max(tmpl.next_run_at, start)
        signed = amount if tmpl.type == TransactionType.INCOME else -amount
        for occ in iter_occurrences(tmpl.cadence, first=first, until=end):
            events.append(
                {
                    "date": occ,
                    "label": tmpl.name,
                    "amount": amount,
                    "signed": signed,
                    "source": "template",
                    "estimated": False,
                    "entity_id": tmpl.id,
                }
            )
            if tmpl.type == TransactionType.EXPENSE:
                scheduled_expense_by_category[tmpl.category_id] = (
                    scheduled_expense_by_category.get(tmpl.category_id, 0.0) + amount
                )

    # ── Pending project payments ─────────────────────────────────────────────
    payments = (
        db.query(ProjectPayment)
        .join(FinancialProject, ProjectPayment.project_id == FinancialProject.id)
        .filter(
            ProjectPayment.status == PaymentStatus.PENDING,
            ProjectPayment.due_date.isnot(None),
            ProjectPayment.due_date >= start,
            ProjectPayment.due_date <= end,
            FinancialProject.deleted_at.is_(None),
        )
        .all()
    )
    for payment in payments:
        amount = float(payment.amount)
        events.append(
            {
                "date": payment.due_date,
                "label": f"{payment.project.name}: payment",
                "amount": amount,
                "signed": -amount,
                "source": "project_payment",
                "estimated": False,
                "entity_id": payment.id,
            }
        )

    # ── Maturing savings bundles ─────────────────────────────────────────────
    bundles = (
        db.query(SavingsBundle)
        .filter(
            SavingsBundle.status == SavingsStatus.ACTIVE,
            SavingsBundle.maturity_date.isnot(None),
            SavingsBundle.maturity_date >= start,
            SavingsBundle.maturity_date <= end,
            SavingsBundle.deleted_at.is_(None),
        )
        .all()
    )
    for bundle in bundles:
        amount = float(bundle.future_amount)
        events.append(
            {
                "date": bundle.maturity_date,
                "label": f"{bundle.name} matures",
                "amount": amount,
                "signed": amount,
                "source": "savings_maturity",
                "estimated": False,
                "entity_id": bundle.id,
            }
        )

    # ── Estimated budget headroom (current fiscal period) ────────────────────
    if include_budget_estimate:
        day = get_month_start_day(db)
        label = current_period_label(date.today(), day)
        p_start, p_end = fiscal_window(label, day)
        rows = compute_budget_rows(db, label, day)

        for row in rows:
            remaining = max(0.0, float(row["monthly_allocation"]) - float(row["this_month_spent"]))
            if remaining <= 0:
                continue

            # Subtract already-scheduled template expenses for this category
            # (within the forecast window) to avoid double counting.
            already_scheduled = scheduled_expense_by_category.get(row["category_id"], 0.0)
            remaining = max(0.0, remaining - already_scheduled)
            if remaining <= 0:
                continue

            applicable_start = max(start, p_start)
            applicable_end = min(p_end, end)
            if applicable_start > applicable_end:
                continue

            n_days = (applicable_end - applicable_start).days + 1
            per_day = remaining / n_days
            category_name = row.get("category_name") or "Budget"
            label_text = f"{category_name} (budget est.)"

            for i in range(n_days):
                events.append(
                    {
                        "date": applicable_start + timedelta(days=i),
                        "label": label_text,
                        "amount": per_day,
                        "signed": -per_day,
                        "source": "budget_estimate",
                        "estimated": True,
                        "entity_id": row["category_id"],
                    }
                )

    events.sort(key=lambda e: e["date"])

    # ── Running balance series ───────────────────────────────────────────────
    series = [{"date": start, "balance": start_balance}]
    balance = start_balance
    for event in events:
        balance += event["signed"]
        series.append({"date": event["date"], "balance": balance})

    # ── Derived outputs ──────────────────────────────────────────────────────
    low_point = min(series, key=lambda p: (p["balance"], p["date"]))
    horizon_net = sum(e["signed"] for e in events)

    shortfall_date = None
    shortfall_balance = None
    for point in series:
        if point["balance"] < buffer:
            shortfall_date = point["date"]
            shortfall_balance = point["balance"]
            break

    shortfall = {
        "breached": shortfall_date is not None,
        "date": shortfall_date,
        "balance": shortfall_balance,
    }

    return {
        "horizon_days": horizon_days,
        "start_date": start,
        "end_date": end,
        "starting_balance": start_balance,
        "currency": currency,
        "buffer": buffer,
        "events": events,
        "series": series,
        "low_point": low_point,
        "horizon_net": horizon_net,
        "shortfall": shortfall,
    }
