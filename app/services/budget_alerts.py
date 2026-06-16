from datetime import date
import logging

from sqlalchemy.orm import Session

from app.services.budget_service import compute_budget_rows
from app.services.fiscal_period import current_period_label, get_month_start_day
from app.services.settings_service import get_setting, get_telegram_config, set_setting

log = logging.getLogger("app.budget_alerts")


def check_and_send_budget_alerts(db: Session) -> None:
    # Release locks held by earlier queries in this scheduler pass before
    # touching budget tables, to avoid lock-order deadlocks with test teardown
    # TRUNCATEs (and long-held idle-in-transaction locks in production).
    db.commit()

    cfg = get_telegram_config(db)
    if cfg.get("telegram_budget_alerts_enabled") != "true":
        return

    today = date.today()
    day = get_month_start_day(db)
    ym = current_period_label(today, day)
    rows = compute_budget_rows(db, ym, day)

    for row in rows:
        if row["monthly_allocation"] <= 0:
            continue

        pct = row["cumulative_pct"]
        new_threshold = 100 if pct >= 100 else (80 if pct >= 80 else 0)
        if new_threshold == 0:
            continue

        key = f"telegram_budget_alert:{ym}:{row['category_id']}"
        prev = int(get_setting(db, key, "0"))

        if new_threshold > prev:
            from app.services.notification_service import publish_notification

            try:
                publish_notification(
                    db,
                    "budget_alert",
                    {
                        "category_name": row["category_name"],
                        "spent": float(row["this_month_spent"]),
                        "limit": float(row["monthly_allocation"]),
                        "pct": float(pct),
                        "threshold": new_threshold,
                    },
                )
                db.commit()
            except Exception:
                log.warning("Failed to publish budget_alert notification", exc_info=True)
            set_setting(db, key, str(new_threshold))
