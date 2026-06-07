# Carange — Family Finance Tracker

A self-hosted personal finance app for tracking a Vietnamese household's daily spending,
savings, investment projects, budget, and assets. Built with FastAPI and SQLite; designed
for LAN access from any device. Runs as three Docker containers on a k3s homelab cluster.

---

## Features

### Dashboard
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

### Transactions
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

### Review Inbox
- Holding area for all auto-imported (OCR/Email) transactions pending confirmation
- Edit description and category before approving; reject to discard
- **Remember as Rule** — one click to turn the approval into a permanent auto-rule
- Approve All bulk action with partial-failure feedback
- Live badge count in every nav viewport syncs after every action

### OCR Import
- Upload screenshots (JPEG/PNG/WebP/HEIC, up to 20 MB) from any payment app
- **Dual extraction path:**
  - Ollama vision (Qwen3.5-9B, self-hosted) — handles any screenshot layout
  - PaddleOCR + source-specific parsers — fallback when Ollama is offline
- Parsers: Timo, UOB, LioBank, Shopee Food, Grab, Generic
- Deduplication by image SHA-256 (re-upload returns the existing job)
- Transactions land in the Review Inbox; auto-approved only if confidence ≥ 0.95

### Email Ingestion
- IMAP polling worker processes forwarded bank notification emails
- Parsers: **Timo** (debit/credit), **Grab** (Bike/Car with pickup→dropoff route in the
  description, Food, Express), **UOB** card alerts, **Shopee**, **Payoo**, Generic LLM fallback
- Same dedup → rules → review pipeline as OCR

### Budget
- **Envelope-style rollover budgets** — unspent balance carries forward as credit;
  overspending rolls as a deficit so over-budget context is always visible
- "Effective from" month picker — set future allocations without touching the current month
- Category name links navigate to Transactions pre-filtered for that category and month
- Budget History modal — last 6 months of spend vs. budget per category with delta indicators
- Summary bar: Total Budgeted / Spent / Available + overall progress bar

### Savings Bundles
- Track fixed deposits, recurring deposits, and savings goals
- Record bank, interest rate, start/maturity dates, current and target values
- **Mark Completed** — auto-creates a matching income transaction for the matured amount
- **Rollover** — closes a bundle and seeds a new one with the maturity value

### Financial Projects
- Multi-step goals: Real Estate, Investment, Vehicle, Education, Vacation, Custom
- Payment schedule with due dates, paid/pending status, and YTD progress
- Progress tracking with deadline risk detection (flagged on Dashboard)

### Other Assets
- Record gold, foreign currency, and other holdings
- Aggregated into Net Worth via a SQL sum (no Python-side iteration)

### Payees & Rules
- **Payees** — canonical names with regex alias patterns; descriptions are normalised
  on every ingest; compiled patterns cached per-process and invalidated on write
- **Rules** — ordered auto-categorisation rules on description, amount, payment method,
  source, or payee; supports auto-approve and force-review actions

### Pulse (Daily Digest)
- Health score (0–4) with green/amber/red level based on income, savings, and budget
- LLM-powered budget commentary via Ollama (degrades gracefully when offline)

### Telegram Notifications
- **Async push** — scalar fields extracted while the session is open, then the HTTP POST
  fires in a background thread so transaction creation is never blocked by network latency
- Separate message format for "Needs Review" (OCR/Email) vs. confirmed transactions
- Review reminder for stale inbox items

### Notes
- Two-panel editor with auto-save (800 ms debounce)
- Types: General, Money Owed; filter by type

---

## Architecture

```
┌────────────────────────────────────────────────────────┐
│  Browser  ←→  FastAPI app  (Jinja2 + HTMX + Tailwind) │
│                    │                                    │
│              SQLite (WAL)  ←→  OCR Worker              │
│                    │                 (PaddleOCR/Ollama) │
│                    └─────────── Email Worker (IMAP)    │
└────────────────────────────────────────────────────────┘
```

| Component | Image | Role |
|-----------|-------|------|
| `carange` | `ghcr.io/thevivotran/carange` | FastAPI web app |
| `carange-ocr-worker` | `ghcr.io/thevivotran/carange-ocr-worker` | OCR import pipeline |
| `carange-email-worker` | `ghcr.io/thevivotran/carange-email-worker` | Email ingestion pipeline |

**Stack:** FastAPI · SQLAlchemy · Pydantic v2 · SQLite (WAL) · Jinja2 · HTMX · Alpine.js ·
Tailwind CSS · Chart.js · Font Awesome · PaddleOCR · Ollama · Telegram Bot API

---

## Running locally

```bash
git clone git@github.com:thevivotran/carange.git
cd carange
pip install -r requirements.txt   # or: uv sync
python main.py                     # → http://localhost:6868
```

**Environment variables** (all optional):

| Variable | Default | Purpose |
|----------|---------|---------|
| `DATABASE_URL` | `sqlite:///./carange.db` | SQLite path |
| `UPLOAD_DIR` | `uploads` | Screenshot storage for OCR jobs |
| `TELEGRAM_BOT_TOKEN` | — | Telegram push notifications |
| `TELEGRAM_CHAT_ID` | — | Target chat ID for notifications |
| `OLLAMA_URL` | — | Ollama endpoint (e.g. `http://localhost:11434`) |
| `REVIEW_THRESHOLD` | `0.95` | Confidence below which a tx enters the Review Inbox |

---

## Self-Hosting

Want to run Carange for your own family? Two Docker Compose setups are provided —
pick the one that matches how much you want to run.

### Quick Start — SQLite (recommended default, ~5 minutes)

No extra services, single container, single data volume.

```bash
git clone git@github.com:thevivotran/carange.git
cd carange
docker compose up -d
# → http://localhost:6868
```

This builds the app image from source (`docker-compose.yml`) and stores everything —
database and uploads — in a `carange_data` volume.

### PostgreSQL path (advanced — worker queues, MATVIEW dashboard, multi-replica)

```bash
docker compose -f docker-compose.pg.yml up -d
# → http://localhost:6868
```

Adds a `postgres:16-alpine` service. Choose this if you plan to run the OCR/email
worker queues, want the materialized-view dashboard, or intend to scale to multiple
app replicas. On first run against an empty database the app creates all tables and
seeds 16 default categories — no manual migration step required.

### Optional features

Both compose files ship with the extras commented out — uncomment what you need:

| Feature | What to configure |
|---------|-------------------|
| Screenshot import (OCR) | Uncomment the `ocr_worker` service |
| Bank email import | Uncomment the `email_worker` service + set `IMAP_*` variables |
| AI budget insights (Pulse) | Set `OLLAMA_URL` (and optionally `OLLAMA_MODEL`) to your self-hosted LLM server |
| Push notifications | Set `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` |

After install, open **Settings** in the app to pick a **display currency** (VND/USD/EUR)
and a **dashboard layout** (Simple/Standard/Full) that matches your family's comfort level.

### Security note

This app has **no authentication layer**. Run it:
- On a local network only, **or**
- Behind a VPN (WireGuard, Tailscale), **or**
- Behind a reverse proxy with auth (Nginx + htpasswd, Authelia, etc.)

**Never expose port 6868 directly to the internet.**

---

## Tests & CI

```bash
make all        # ruff lint + ui-lint design-token check + tests + coverage ≥ 95%
make test-fast  # fast pytest run without coverage
```

**677 tests** across 37 modules using in-memory SQLite — production DB is never touched.

CI builds Docker images on every push to `main`:
```
ghcr.io/thevivotran/carange:main-YYYYMMDD-HHmmss-SHA
```

---

## Deployment

Deployed on a k3s homelab cluster via FluxCD GitOps. Manifests live in `homelab/apps/carange/`.
Push to `main` → GitHub Actions builds and pushes all three images → FluxCD detects the new
tags and rolls out the updated pods automatically.

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

---

## License

[MIT](LICENSE)
