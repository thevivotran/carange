# Features

Full feature reference for Carange. For a quick overview, screenshots, and self-hosting
instructions, see the [main README](../README.md).

---

## Dashboard

- **9 KPI cards** — Liquid Savings Rate, Real Estate Rate, Net Cash, Living Expense Ratio,
  Emergency Fund Coverage, Budget Health, Runway, FI Progress, Net Worth Growth — each with
  a formula popover and month-over-month delta arrow
- **Net Worth card** — gradient panel breaking down Cash on Hand, Active Savings, Real Estate
  Equity, Other Assets, and Passive Income
- **BDS (Real Estate) progress panel** — next payment urgency, YTD progress, completion date
- **One-Income Stress Test** — checks whether a single salary covers all monthly obligations
- **Cash Outlook card** — compact forward-looking balance projection showing the low point and
  a shortfall warning for the next 30 days; toggleable via the `cash_outlook` section setting
- **Month navigation** — KPI cards and charts update when the month selector changes
- **Cash flow chart** (6-month bar+line), **Wealth Building trend** (6-month stacked bar
  with 3-month rolling savings rate), **Wealth Allocation donut**
- **Alerts panel** — maturing savings within 30 days, over-budget categories, open advances
- Budget category links navigate to transactions pre-filtered for that category and month
- **Layout presets** — Simple / Standard / Full, so the dashboard can be as minimal or as
  dense as a household wants; gates which sidebar/bottom-nav items are shown too

## Onboarding

- **Welcome banner** on the dashboard walks new users through the first four steps
  (add a transaction, review categories, set a budget, explore Pulse) and dismisses permanently
- **Sample data** — load ~2 months of synthetic transactions and a sample savings goal from
  Settings to see a populated dashboard; clearly tagged and fully reversible with one click
- New installs default to the **Simple dashboard layout** (~6 KPI cards instead of 20+
  empty metrics); inline help text explains "rollover," "personal advance," and the
  Savings Bundle types for first-time users

## Custom Fiscal Month

- **Pay-cycle start day** (`month_start_day` setting, 1–28, default 1) — monthly KPIs and
  budgets run from that day of one month to the day before it in the next month
- A period is labeled by its start month, so if your pay cycle starts on the 15th,
  "February" covers Feb 15 → Mar 14
- Logic lives in `app/services/fiscal_period.py`
- The PostgreSQL `mv_monthly_totals` materialized view gates off when `month_start_day ≠ 1`
  (it aggregates by calendar month)

## Budget

- **Envelope-style rollover budgets** — unspent balance carries forward as credit;
  overspending rolls as a deficit so over-budget context is always visible
- **Budget-aware transactions** — every transaction form shows the remaining budget for the
  selected category in real time. The Quick-Add modal also displays a budget bar via HTMX.
- **Budget bar in notification cards** — Telegram messages for ingested transactions include
  a budget bar when the category has an allocation (matches the in-app drawer view)
- "Effective from" month picker — set future allocations without touching the current month
- **Budget History modal** — last 6 months of spend vs. budget per category with delta
- **Summary bar** — Total Budgeted / Spent / Available + overall progress bar
- **Alert thresholds** — notifications fire as categories approach overspend

## Cash-Flow Forecast

- **Forward projection** — projects the running cash balance over 30/60/90 day horizons
- **Data sources** — unifies recurring `transaction_templates`, PENDING `project_payments`,
  maturing `savings_bundles`, and estimated budget headroom
- **Low-point & shortfall warnings** — surfaces the projected low point and warns if the
  balance dips below the `forecast_buffer` setting
- **Goal-funding settle** — a transaction categorized to a project can settle a matching
  PENDING milestone (`POST /api/transactions/{id}/settle-payment/{payment_id}`,
  suggestion via `GET /api/projects/{id}/payments/match`), so paid milestones drop off
  the forecast
- Cash Outlook card on the dashboard provides a quick at-a-glance summary
- Pure read-only logic in `app/services/forecast_service.py`; cadence stepping shared
  with the scheduler via `app/services/cadence.py`

## Transactions

- Log income and expense with date, category, description, and payment method
- Advanced filters: date range, type, category, keyword, project, source (OCR/Email/Manual),
  advance status; URL-driven deep links from Dashboard, Budget, and Forecast pages
- **?focus=<tx_id> deep-link** — visiting `/transactions?focus=123` fetches the transaction
  and opens the detail drawer immediately
- Month navigator synced with Dashboard and Budget pages
- Soft-delete with undo toast; audit log on every edit
- **Cascade protection** — warns before deleting a transaction linked to a savings bundle or
  project payment
- **Advance tracking** — mark personal advances, settle them when repaid
- Bulk CSV export respecting active filters
- Quick-entry via Templates

## Review Inbox

- Holding area for all auto-imported (OCR/Email) transactions pending confirmation
- Edit description and category before approving; reject to discard
- **Remember as Rule** — one click to turn the approval into a permanent auto-rule
- Approve All bulk action with partial-failure feedback
- Live badge count in every nav viewport syncs after every action

## OCR Import

- Upload screenshots (JPEG/PNG/WebP/HEIC, up to 20 MB) from any payment app
- **Triple extraction path:**
  1. **Ollama vision** (Qwen3.5-9B, self-hosted) — handles any screenshot layout with
     a single vision LLM call
  2. **PaddleOCR 3.x + source-specific parsers** — fallback when Ollama is offline.
  3. **AI fallback loop** — generates custom regex parsers for unfamiliar screenshot
     formats via vLLM. **Generated parsers require human approval** before activation.
- Parsers: Timo, UOB, LioBank, Shopee Food, Grab, Generic
- AI-generated parsers are reviewed via **Import → OCR Screenshots → Pending Parsers**
- Deduplication by image SHA-256 (re-upload returns the existing job)
- Transactions land in the Review Inbox; auto-approved only if confidence ≥ 0.95

See [`ocr_worker/README.md`](../ocr_worker/README.md) for the worker's internals.

## Email Ingestion

- IMAP polling worker processes forwarded bank notification emails
- Parsers: **VCB** (Vietcombank), **UOB** card alerts, **Payoo**, **VNPay**, **Shopee**,
  **Grab** (Bike/Car with pickup→dropoff route in the description, Food, Express),
  **Timo** (debit/credit), **LearnedRegex**, **GenericOllama** LLM fallback
- **Learned patterns** — when the LLM successfully parses an email from an unknown sender,
  it derives regex patterns stored in the database; subsequent emails skip the LLM call.
  Patterns are auto-dropped after 5 consecutive misses (sender changed their template).
- **AI parser human approval gate** — LLM-generated regex parsers require manual approval
  before activation, preventing AI-generated code from running without review.
  Approvals managed via **Import → Email Receipts → Approved Parsers**.
- Same dedup → rules → review pipeline as OCR
- **LLM-unavailable retry** — when the GPU node is offline, emails stay `pending` and
  retry every 30 minutes without consuming retry attempts, so they're processed
  automatically once the model is back

See [`email_worker/README.md`](../email_worker/README.md) for the worker's internals.

## Savings Bundles

- Track fixed deposits, recurring deposits, and savings goals
- Record bank, interest rate, start/maturity dates, current and target values
- **Mark Completed** — auto-creates a matching income transaction for the matured amount
- **Rollover** — closes a bundle and seeds a new one with the maturity value

## Financial Projects

- Multi-step goals: Real Estate, Investment, Vehicle, Education, Vacation, Custom
- Payment schedule with due dates, paid/pending status, and YTD progress
- Progress tracking with deadline risk detection (flagged on Dashboard)
- **Milestone matching** — the forecast endpoint suggests pending payments that can be
  settled by a new transaction

## Other Assets

- Record gold, foreign currency, and other holdings
- Aggregated into Net Worth via a SQL sum (no Python-side iteration)

## Payees & Rules

- **Payees** — canonical names with regex alias patterns; descriptions are normalised
  on every ingest; compiled patterns cached per-process and invalidated on write
- **Rules** — ordered auto-categorisation rules on description, amount, payment method,
  source, or payee; supports auto-approve and force-review actions

## Pulse (Daily Digest)

- AI-generated morning briefing on yesterday's transactions, week-over-week spend changes,
  budget status, and savings/yield comparisons
- Health score (0–4) with green/amber/red level based on income, savings, and budget
- LLM-powered budget commentary via Ollama (degrades gracefully when offline)

## Telegram Notifications

Carange sends push notifications via a **durable event-driven queue** (`notification_events`
PostgreSQL table + `notify_worker`). Notifications are never lost — if the Telegram API
is unavailable, the worker retries with exponential backoff.

### Notification types

| Type | Trigger | Content |
|------|---------|---------|
| **New transaction (ingested)** | OCR / Email import | Amount, category, description, budget bar, inline View/Budget buttons |
| **Advance ping** | Personal advance created/updated | Amount, category, description, link to unsettled advances |
| **Review reminder** | Periodic (stale Review Inbox) | Count of pending items, link to Review Inbox |
| **Budget alert** | Category nearing/over budget | Budget bar, percentage spent, limit, status line, link to Budget |

### Message format

- **Amount first** (bold) — `+45,000đ — Food` or `-150,000đ — Transport`
- **Description** (italic) on the next line
- **Budget bar** appended when the category has an allocation: `▰▰▰▰▰▰▰▰▰▰▱ 87%  Near limit ⚠️`
- A clean `———` divider separates sections

### Inline buttons

- **🔍 View** — deep links to the transaction detail drawer (`/transactions?focus=<id>`)
- **📊 View budget** — deep link to the Budget page
- **📥 Review inbox** — deep link to `/transactions?needs_review=true`
- **📌 View advances** — deep link to unsettled advances

Buttons require `APP_URL` to be configured (the app's public URL). Without it, inline
keyboards are omitted and text-only messages are sent.

### Privacy features

- **Spoiler-hide amounts** — when `TELEGRAM_HIDE_AMOUNTS=true`, all monetary values are
  wrapped in `<tg-spoiler>` tags, hidden behind a tap-to-reveal in Telegram
- **Income transactions** skip the "View budget" button (irrelevant for income)
- **Needs-review vs confirmed** — transactions pending review show a ⚠️ header and only
  the Review Inbox button

### Architecture

```
FastAPI app                Notify worker              Telegram API
    │                           │                        │
    │ INSERT notification_event │                        │
    │ SELECT pg_notify(...)    │                        │
    └─────────────────────────►│                        │
    │                          │ LISTEN + claim event    │
    │                          │ build HTML message      │
    │                          │ POST sendMessage ──────►│
    │                          │                        │
    │                          │ (retry on failure with  │
    │                          │  exponential backoff)   │
```

The notify worker runs as part of the main app image (no separate container needed).
Event types: `advance_ping`, `tx_ingested`, `review_reminder`, `budget_alert`.

See [`notify_worker/README.md`](../notify_worker/README.md) for the worker's internals.

## Notes

- Two-panel editor with auto-save (800 ms debounce)
- Types: General, Money Owed; filter by type

---

## Adaptive UX/UI

- **Responsive shell** — collapsible sidebar navigation on desktop/tablet collapses to a
  fixed bottom nav bar on mobile; layout presets (Simple/Standard/Full) further declutter
  the experience for the household's comfort level
- **Dark mode** — system-preference aware (`prefers-color-scheme`), togglable from the nav,
  persisted per-browser
- **HTMX-driven fragments** — dashboard cards, filters, and modals update in place without
  full-page reloads, keeping interactions snappy on slower mobile connections

---

## Design system

`tests/test_ui_lint.py` runs as part of `make lint` and enforces 7 template rules to prevent UI drift:

| Rule | Prevents |
|------|---------|
| `badge-font-medium` | Status badge text rendered thin |
| `icon-button-size` | Header icon buttons at inconsistent sizes |
| `modal-button-font` | Modal footer buttons without `font-medium` |
| `page-heading-color` | Page `h2` titles missing `text-gray-800` |
| `icon-btn-label` | Icon-only buttons with no `title` or `aria-label` |
| `input-focus-ring` | Bordered inputs missing `focus:ring-2` |
| `img-alt` | `<img>` tags without `alt` attribute |
