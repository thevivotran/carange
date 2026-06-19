# Carange — Family Finance Tracker

[![CI / Build](https://github.com/thevivotran/carange/actions/workflows/build.yaml/badge.svg)](https://github.com/thevivotran/carange/actions/workflows/build.yaml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Conventional Commits](https://img.shields.io/badge/commits-conventional-fe5196.svg)](https://www.conventionalcommits.org/)

A self-hosted personal finance app for tracking a Vietnamese household's daily spending,
savings, investment projects, budget, and assets. Built with FastAPI and PostgreSQL; designed
to feel native on your phone and your desktop alike. Runs as Docker containers — at home,
on a NAS, or on a k3s cluster.

Auto-import via email forwarding and OCR screenshots, push notifications via Telegram,
and AI-powered budget digests — all optional, all self-contained.

<p align="center">
  <img src="docs/images/dashboard-desktop-light.png" alt="Carange dashboard on desktop" width="68%">
  <img src="docs/images/dashboard-mobile-light.png" alt="Carange dashboard on mobile" width="27%">
</p>

---

## Why Carange

- **One dashboard, the full picture** — net worth, cash flow, budgets, savings, real estate
  and investment projects, all in one place, with KPI cards that explain their own formulas.
  Switch between Simple / Standard / Full layout to match your comfort level.

- **Budget-aware transactions** — every transaction shows remaining budget for its category
  right in the form. Category-level monthly budgets with rollover, progress bars, and alerts
  when you're about to overshoot.

- **Cash-flow forecast** — projects your running balance over 30/60/90 days by unifying
  recurring templates, pending project milestones, maturing savings, and budget headroom.
  Surfaces the projected low point and shortfall warnings.

- **Auto-import, not manual entry** — forward bank emails or upload payment-app screenshots;
  Ollama vision + PaddleOCR 3.x parsers turn them into transactions that land in a Review
  Inbox for a quick confirm. AI fallback loop handles unfamiliar bank formats.

- **Custom fiscal month** — configure your pay-cycle start day (1–28). Monthly KPIs and
  budgets run from that day to the day before it in the next month.

- **Push notifications via Telegram** — real-time alerts on new transactions, budget
  warnings, and a daily Pulse AI digest. Amounts can be spoiler-hidden. Inline buttons
  link directly to the transaction detail.

- **Looks great everywhere** — a responsive shell that's a collapsible sidebar on desktop and
  a bottom nav bar on mobile, with dark mode and HTMX-powered interactions that feel instant
  on any connection.

- **Built for newcomers too** — a guided onboarding banner, one-click sample data, and
  progressive layout disclosure so a new household can see the app "alive" in minutes.

| | |
|---|---|
| ![Dashboard, dark mode](docs/images/dashboard-desktop-dark.png) | ![Mobile dashboard, dark mode](docs/images/dashboard-mobile-dark.png) |
| ![Transactions](docs/images/transactions-desktop-light.png) | ![Review Inbox](docs/images/review-inbox-desktop-light.png) |

See [`docs/FEATURES.md`](docs/FEATURES.md) for the full feature reference (Dashboard,
Transactions, Budget, Savings Bundles, Financial Projects, Pulse AI digest, Telegram
notifications, Cash-flow Forecast, and more).

---

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│  Browser  ←→  FastAPI app  (Jinja2 + HTMX + Alpine.js)   │
│                    │              Tailwind + Chart.js)   │
│              PostgreSQL                                  │
│                    │                                     │
│         ┌──────────┼──────────┐                          │
│         ▼          ▼          ▼                          │
│   OCR Worker   Email W.   Notify Worker                  │
│   (PaddleOCR   (IMAP      (Telegram event queue)         │
│    /Ollama)     poll)                                    │
└──────────────────────────────────────────────────────────┘
```

| Component | Image | Role |
|-----------|-------|------|
| `carange` | `ghcr.io/thevivotran/carange` | FastAPI web app |
| `carange-ocr-worker` | `ghcr.io/thevivotran/carange-ocr-worker` | OCR import pipeline ([details](ocr_worker/README.md)) |
| `carange-email-worker` | `ghcr.io/thevivotran/carange-email-worker` | Email ingestion pipeline ([details](email_worker/README.md)) |
| `carange-notify-worker` | (built into app image) | Durable Telegram notification queue ([details](notify_worker/README.md)) |

**Stack:** FastAPI · SQLAlchemy · Pydantic v2 · PostgreSQL · Alembic · Jinja2 ·
HTMX · Alpine.js · Tailwind CSS · Chart.js · Font Awesome · PaddleOCR 3.x ·
Ollama · Telegram Bot API

---

## Self-Hosting

Want to run Carange for your own family?

### Quick Start (~5 minutes)

```bash
git clone git@github.com:thevivotran/carange.git
cd carange
docker compose up -d
# → http://localhost:6868
```

This builds the app image from source and runs it alongside a `postgres:16-alpine`
service, storing the database and uploads in named volumes. On first run against an
empty database, the app creates all tables and seeds 16 default categories — no
manual migration step required.

### Optional features

The compose file ships with the extras commented out — uncomment what you need:

| Feature | What to configure |
|---------|-------------------|
| Screenshot import (OCR) | Uncomment the `ocr_worker` service |
| Bank email import | Uncomment the `email_worker` service + set `IMAP_*` variables |
| AI budget insights (Pulse) | Set `OLLAMA_URL` (and optionally `OLLAMA_MODEL`) to your self-hosted LLM server |
| Push notifications | Set `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` (and `APP_URL` for deep links) |

After install, open **Settings** in the app to pick a **display currency** (VND/USD/EUR)
and a **dashboard layout** (Simple/Standard/Full) that matches your family's comfort level.
New installs default to the Simple layout with a guided onboarding banner and optional
sample data — see [`docs/FEATURES.md#onboarding`](docs/FEATURES.md#onboarding).

### Security note

This app has **no authentication layer**. Run it:
- On a local network only, **or**
- Behind a VPN (WireGuard, Tailscale), **or**
- Behind a reverse proxy with auth (Nginx + htpasswd, Authelia, etc.)

**Never expose port 6868 directly to the internet.**

Found a security issue? See [`SECURITY.md`](SECURITY.md) for how to report it.

---

## Running locally (development)

### Prerequisites

- Python 3.12+
- PostgreSQL 16+ (or `docker compose -f docker-compose.dev.yml up postgres`)

```bash
git clone git@github.com:thevivotran/carange.git
cd carange
uv sync                      # or: pip install -r requirements.txt
python main.py               # → http://localhost:6868
```

**Environment variables** (all optional):

| Variable | Default | Purpose |
|----------|---------|---------|
| `DATABASE_URL` | `postgresql://carange:***@localhost:5432/carange` | PostgreSQL connection string |
| `UPLOAD_DIR` | `uploads` | Screenshot storage for OCR jobs |
| `TELEGRAM_BOT_TOKEN` | — | Telegram push notifications |
| `TELEGRAM_CHAT_ID` | — | Target chat ID for notifications |
| `TELEGRAM_HIDE_AMOUNTS` | `false` | Spoiler‑hide amounts in notifications |
| `APP_URL` | — | Public URL for Telegram deep‑links (e.g. `https://carange.example.com`) |
| `OLLAMA_URL` | — | Ollama endpoint (e.g. `http://localhost:11434`) |
| `OLLAMA_MODEL` | `llama3.1` | LLM model for Pulse / AI parser fallback |
| `REVIEW_THRESHOLD` | `0.95` | Confidence below which a tx enters the Review Inbox |

```bash
make all        # ruff lint + design-token check + tests + coverage ≥ 95%
make test-fast  # fast pytest run without coverage
```

**~1,080 tests** — PostgreSQL‑backed via testcontainers on CI, in-memory SQLite for
fast local iteration. Production DB is never touched.

---

## Deployment & CI

CI builds Docker images on every push to `main`:
```
ghcr.io/thevivotran/carange:main-YYYYMMDD-HHmmss-SHA
```

Deployed on a k3s homelab cluster via FluxCD GitOps. Manifests live in
`homelab/apps/carange/`. Push to `main` → GitHub Actions builds and pushes all
images → FluxCD detects the new tags and rolls out the updated pods automatically.

---

## Recent Notable Changes

- **Budget‑aware transactions** — category‑level monthly budgets with real‑time
  remaining‑budget indicators in transaction forms, the transaction list, and
  Telegram notifications
- **Cash‑flow forecast** — 30/60/90‑day forward projection of running balance
  with shortfall warnings
- **Custom fiscal month** — pay‑cycle start day configurable per account (1–28);
  monthly KPIs adjust accordingly
- **Telegram notifications** — durable event‑driven queue for push notifications
  with inline action buttons, spoiler‑hide for amounts, and daily Pulse digest
- **Deep links** — Telegram messages link directly to the transaction detail
  drawer via `?focus=<tx_id>`
- **PaddleOCR 3.x upgrade** — Vietnamese bank‑specific parsers with AI fallback
  loop for unfamiliar formats
- **Database migration** — migrated from SQLite to PostgreSQL; Alembic for schema
  migrations

---

## Contributing

Issues and pull requests are welcome — see [`CONTRIBUTING.md`](CONTRIBUTING.md) for the
development workflow, code style, and test requirements. Notable changes are tracked in
[`CHANGELOG.md`](CHANGELOG.md).

---

## License

[MIT](LICENSE)
