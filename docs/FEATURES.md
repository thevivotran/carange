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

## Transactions
- Log income and expense with date, category, description, and payment method
- Advanced filters: date range, type, category, keyword, project, source (OCR/Email/Manual),
  advance status; URL-driven deeplinks from Dashboard and Budget pages
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
- **Dual extraction path:**
  - Ollama vision (Qwen3.5-9B, self-hosted) — handles any screenshot layout
  - PaddleOCR + source-specific parsers — fallback when Ollama is offline
- Parsers: Timo, UOB, LioBank, Shopee Food, Grab, Generic
- Deduplication by image SHA-256 (re-upload returns the existing job)
- Transactions land in the Review Inbox; auto-approved only if confidence ≥ 0.95

See [`ocr_worker/README.md`](../ocr_worker/README.md) for the worker's internals.

## Email Ingestion
- IMAP polling worker processes forwarded bank notification emails
- Parsers: **Timo** (debit/credit), **Grab** (Bike/Car with pickup→dropoff route in the
  description, Food, Express), **UOB** card alerts, **Shopee**, **Payoo**, Generic LLM fallback
- Same dedup → rules → review pipeline as OCR

See [`email_worker/README.md`](../email_worker/README.md) for the worker's internals.

## Budget
- **Envelope-style rollover budgets** — unspent balance carries forward as credit;
  overspending rolls as a deficit so over-budget context is always visible
- "Effective from" month picker — set future allocations without touching the current month
- Category name links navigate to Transactions pre-filtered for that category and month
- Budget History modal — last 6 months of spend vs. budget per category with delta indicators
- Summary bar: Total Budgeted / Spent / Available + overall progress bar

## Savings Bundles
- Track fixed deposits, recurring deposits, and savings goals
- Record bank, interest rate, start/maturity dates, current and target values
- **Mark Completed** — auto-creates a matching income transaction for the matured amount
- **Rollover** — closes a bundle and seeds a new one with the maturity value

## Financial Projects
- Multi-step goals: Real Estate, Investment, Vehicle, Education, Vacation, Custom
- Payment schedule with due dates, paid/pending status, and YTD progress
- Progress tracking with deadline risk detection (flagged on Dashboard)

## Other Assets
- Record gold, foreign currency, and other holdings
- Aggregated into Net Worth via a SQL sum (no Python-side iteration)

## Payees & Rules
- **Payees** — canonical names with regex alias patterns; descriptions are normalised
  on every ingest; compiled patterns cached per-process and invalidated on write
- **Rules** — ordered auto-categorisation rules on description, amount, payment method,
  source, or payee; supports auto-approve and force-review actions

## Pulse (Daily Digest)
- Health score (0–4) with green/amber/red level based on income, savings, and budget
- LLM-powered budget commentary via Ollama (degrades gracefully when offline)

## Telegram Notifications
- **Async push** — scalar fields extracted while the session is open, then the HTTP POST
  fires in a background thread so transaction creation is never blocked by network latency
- Separate message format for "Needs Review" (OCR/Email) vs. confirmed transactions
- Review reminder for stale inbox items

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
