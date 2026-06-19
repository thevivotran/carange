# Notify Worker — Durable Telegram Notification Queue

[![CI / Build](https://github.com/thevivotran/carange/actions/workflows/build.yaml/badge.svg)](https://github.com/thevivotran/carange/actions/workflows/build.yaml)

A standalone background worker that processes the `notification_events` queue —
delivering Telegram messages for transactions, budget alerts, review reminders,
and advance-ping signals. PostgreSQL-only (uses `LISTEN/NOTIFY` + `SELECT FOR
UPDATE SKIP LOCKED`).

---

## Architecture

```
FastAPI app             Notify worker
    │                        │
    │ INSERT INTO            │ LISTEN "telegram_notifications"
    │ notification_events    │        + SELECT FOR UPDATE
    │                        │
    ▼                        ▼
┌──────────────────────────────────────────────┐
│            PostgreSQL                         │
│  notification_events table                    │
│    • advance_ping                             │
│    • tx_ingested                              │
│    • review_reminder                          │
│    • budget_alert                             │
└──────────────────────────────────────────────┘
```

The worker uses PostgreSQL's `LISTEN`/`NOTIFY` for instant wake-up when a new
event is queued, plus `SELECT FOR UPDATE SKIP LOCKED` for safe concurrent
claiming. It's designed to be safe to run multiple replicas — only one worker
claims each event.

---

## Event Types

| Event | Trigger | Behavior |
|-------|---------|----------|
| `advance_ping` | Personal advance created/updated | Sends formatted "Personal advance — Created/Updated" card with link to unsettled advances |
| `tx_ingested` | Transaction imported via email/OCR | "New [Email/OCR]" card with budget bar, View button, and review status |
| `review_reminder` | Scheduled daily/periodic | "N transactions pending review" reminder prompt |
| `budget_alert` | Budget threshold exceeded | Alert card with budget bar, percentage spent, and link to Budget page |

---

## How it works

1. **Event claiming** — `_claim_next()` uses `SELECT FOR UPDATE SKIP LOCKED`
   to atomically claim the oldest `PENDING` event. Also reclaims events stuck
   in `PROCESSING` past the `STUCK_TIMEOUT_MIN` threshold, and permanently
   fails events that exceed `MAX_RETRIES`.

2. **Message building** — `_build_message()` dispatches on `event_type` and
   assembles an HTML-formatted Telegram message with optional inline keyboard
   buttons.

3. **Delivery** — `_send()` posts the message to the Telegram Bot API. On
   failure, `_handle_failure()` schedules an exponential backoff retry
   (2min → 4min → 8min, up to `MAX_RETRIES`).

4. **LISTEN loop** — After draining the queue, the worker blocks on
   `LISTEN "telegram_notifications"` via `psycopg2`. A new `NOTIFY` from the
   app wakes it up instantly.

5. **Liveness heartbeat** — Touches `/tmp/worker_alive` every 30s while
   making progress. If the worker stops processing (stuck/crashed), the
   heartbeat stops and Kubernetes can detect the failure.

---

## Running

### Standalone (development)

```bash
cd carange
uv sync                              # or: pip install -r requirements.txt
DATABASE_URL=postgresql://carange:***@localhost:5432/carange \
  python -m notify_worker.worker
```

### Docker

```bash
docker build -t carange-notify-worker -f notify_worker/Dockerfile .
docker run \
  -e DATABASE_URL=postgresql://carange:***@postgres:5432/carange \
  carange-notify-worker
```

### Docker Compose

Uncomment the `notify_worker` service in `docker-compose.yml`:

```yaml
notify_worker:
  build:
    context: .
    dockerfile: notify_worker/Dockerfile
  environment:
    - DATABASE_URL=postgresql://carange:***@postgres:5432/carange
  depends_on:
    postgres:
      condition: service_healthy
  restart: unless-stopped
```

---

## Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `DATABASE_URL` | `postgresql://carange:***@localhost:5432/carange` | PostgreSQL connection string |
| `STUCK_TIMEOUT_MIN` | `30` | Minutes before a PROCESSING event is reclaimed |
| `MAX_RETRIES` | `3` | Delivery attempts before permanent failure |

---

## Failure & Retry

- **Transient failure** (network blip, Telegram API hiccup) → exponential
  backoff: 2min → 4min → 8min (up to `MAX_RETRIES`)
- **Permanent failure** (exhausted retries) → status set to `FAILED`, error
  message recorded in the event row
- **Stuck events** (worker crashed mid-processing) → reclaimed after
  `STUCK_TIMEOUT_MIN` of inactivity; if already at max retries, marked
  permanently failed

---

## Testing

```bash
cd carange
uv run pytest tests/test_notify_worker.py -v
```

8 tests covering:
- Basic event claiming and empty-queue behaviour
- Retry-after future-skip logic
- Failure backoff scheduling and permanent-failure transition
- Message formatting for each event type (`advance_ping`, `review_reminder`,
  unknown event)
- Edge cases: zero-count review reminders, unknown event types

---

## Key Files

| File | Purpose |
|------|---------|
| `notify_worker/worker.py` | Main worker loop: claiming, building, sending |
| `notify_worker/Dockerfile` | Container image (python:3.14-slim) |
| `app/notify/telegram.py` | Message formatting helpers (`_send`, `_fire`, `_build_card_text`, `_budget_bar_line`, `inline_url_keyboard`) |
| `app/models/database.py` | `NotificationEvent` model + `NotificationEventStatus` enum |
| `app/services/settings_service.py` | `get_telegram_config()` — resolves credentials from DB or env vars |
| `tests/test_notify_worker.py` | 8 unit tests |
