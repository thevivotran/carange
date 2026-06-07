# Onboarding Evaluation & Improvement Plan for Carange (Open-Source Release)

## Re-evaluation Update (2026-06-07)

Since this plan was first written, four things changed in the codebase:

1. **PostgreSQL is now a first-class deployment option.** A run of commits added PostgreSQL-native support (NUMERIC money, TIMESTAMPTZ, JSONB, MATVIEW dashboard, full migration system, PG test suite). This affects Solution 1 — there are now two valid self-hosting paths (SQLite vs. PostgreSQL), not one.
2. **Nav simplification landed** (`084e016`): Categories, Templates, Rules, Payees were removed from the sidebar in favor of a single Settings gear link. This partially closes the "13 feature areas exposed equally" gap called out below — 11 nav items remain instead of 13.
3. **Display currency selector landed** (`ad7686d`): Settings now exposes a VND/USD/EUR display-format picker — a `currency_format` service (cached, invalidated on save) plus Jinja filters/globals registered across all template environments switch the symbol and placement (₫ suffix / $ prefix / € suffix) for every rendered amount. It's purely cosmetic — stored values stay in VND, nothing is converted. **This closes the "Currency hardcoded to VND (₫)" gap** outright, and **invalidates the "No currency configurability" scope boundary** below — that boundary should be reworded to "no currency *conversion* / multi-currency accounting," which remains correctly out of scope.
4. **Dashboard layout presets landed** (`3dc2db4`): Settings now has a Simple / Standard / Full preset (`dashboard_layout` setting + `app/services/dashboard_layout.py`) that gates which dashboard sections render. Core health KPIs, Net Worth, Safety Score, Alerts, and Recent Transactions always show; Cash Flow, Budget Snapshot, extra KPI cards, Active Projects, Savings Goals, Wealth Building, and Stress Test are hidden under "Simple." **This is a major, direct step toward closing the "Dashboard shows 20+ empty metrics immediately" gap** — a new user can now switch to "Simple" (ideally as a sensible new-user default) and see a half-dozen cards instead of 20+.

Together, items 3 and 4 give users a genuine **new ability to enhance/personalize their setup** post-install — pick a display currency that matches their household, and pick a dashboard density that matches their comfort level — without touching code or config files. That's real onboarding progress that this plan didn't originally anticipate landing this fast.

### Status of each solution

| # | Solution | Status |
|---|----------|--------|
| 1 | `docker-compose.yml` | **Needs rework** — a `docker-compose.yml` exists, but it's a dev-only PostgreSQL service (just `postgres:16-alpine`, no app). The self-hosting compose this plan describes was never created. |
| 2 | Welcome banner | Not started — no onboarding partials, no `show_onboarding` wiring in `main.py`. **Adjacent progress:** dashboard layout presets (`3dc2db4`) now let a user shrink the dashboard to ~6 cards via Settings — the banner's "where do I start" job remains undone, but the "overwhelming wall of zeroes" symptom it was meant to treat is now optional. |
| 3 | AI prompt language fix | Not started — `insight_service.py:38,46` still hardcodes `"Viết bằng tiếng Việt."` |
| 4 | Empty state improvements | Not started — though the layout-preset work (`3dc2db4`) takes a different, complementary path to the same goal: hiding empty sections rather than dressing them up with CTAs |
| 5 | README + `.env.example` | Half done — `.env.example` exists; README has no Quick Start / self-hosting section. Should now also document the new Settings-page currency picker and dashboard layout presets as post-install personalization steps |

### Revised plan for Solution 1

Two legitimate deployment paths now exist:
- **SQLite** — simple, no extra service, best default for families. The original proposed compose below is correct for this path.
- **PostgreSQL** — needed for worker queues (OCR/email), MATVIEW dashboard, multi-pod setups. More complex, more capable.

Approach: rename the existing dev-only compose to `docker-compose.dev.yml`, create the SQLite-based self-hosting compose (as originally proposed) as `docker-compose.yml`, and add a `docker-compose.pg.yml` for PostgreSQL self-hosters. The README (Solution 5) needs to document both paths.

Everything else in this plan — Solutions 2, 3, 4, the scope boundaries, and the implementation order — remains valid as written.

---

## Context

Carange is being open-sourced so other families can self-host it. Currently, the app has **zero explicit onboarding** — a new user lands directly on a complex dashboard with 20+ KPI cards, charts, and metrics all showing zeroes, with no guidance on what to do first. This document evaluates the current gaps and proposes concrete, high-value improvements ordered by impact.

---

## Evaluation: What a New User Experiences Today

### First-Time App Load

1. User opens `http://localhost:6868/` and lands on the **Dashboard** — no welcome, no redirect
2. SQLAlchemy auto-creates 18 tables; 16 categories are seeded (Food, Transport, Salary, etc.)
3. The dashboard renders immediately with all metrics showing **0 or N/A**

### What's on the Dashboard (All Empty on Day 1)

- **Family Safety Score** — 4-dot indicator; new user scores 0/4 with red alerts
- **9 KPI Cards** — Liquid Savings Rate, Net Cash, Emergency Fund Coverage, Net Worth, etc.
- **6 Charts** — Cash flow trend (6 months), budget snapshot, wealth building analysis
- **One-Income Stress Test** — collapsible section
- **Alerts Panel** — empty
- **Recent Transactions** — "No transactions yet"
- **Active Projects** — empty

### App Feature Scope (13 Distinct Concepts)

| Section | Features |
|---------|----------|
| TODAY | Dashboard, Transactions, Import (OCR), Pulse (AI), Review Inbox |
| PLANNING | Budget, Savings Bundles, Assets, Projects |
| SETTINGS | Categories, Templates, Notes/IOUs, Rules, Payees |

---

## What Works Well (Keep These)

- **16 categories auto-seeded** — user doesn't need to build a taxonomy from scratch
- **KPI card popovers** — each metric has an info button explaining its formula
- **Graceful degradation** — Telegram, IMAP, and LLM features all optional; app fully usable without them
- **Safe delete UX** — 5-second undo toast + 30-day trash mode
- **Mobile bottom nav** — clear primary actions from any page
- **Empty state CTAs** — a few pages already have "no data" links (Budget, Savings)
- **Collapsible sections** — Wealth Building and Stress Test are hidden by default on the dashboard
- **Dashboard layout presets** *(new, `3dc2db4`)* — Simple/Standard/Full picker in Settings lets a user shrink a 20+ card dashboard down to ~6 essentials in two clicks, no code or env vars
- **Display currency selector** *(new, `ad7686d`)* — VND/USD/EUR picker in Settings switches the symbol and placement shown everywhere (cosmetic, no conversion), removing a real adoption barrier for non-Vietnamese self-hosters

---

## Critical Gaps (Overwhelming for New Users)

| Gap | Severity | Impact |
|-----|----------|--------|
| No welcome banner or setup wizard | 🔴 High | User has no idea where to start |
| Dashboard shows 20+ empty metrics immediately | 🟡 ~~High~~ Reduced — opt-in fix shipped (`3dc2db4`) | A "Simple" layout preset in Settings now collapses the dashboard to ~6 cards. Cognitive overload is now *avoidable*, but the default is still "Full" and there's still no prompt steering new users toward it |
| No `docker-compose.yml` for self-hosters | 🔴 High | Families can't easily run the app |
| LLM prompts hardcoded in Vietnamese | 🔴 High | Pulse/AI features useless for non-Vietnamese users |
| 13 feature areas exposed equally from day one | 🟡 Medium → improving | Nav simplification (`084e016`, 11 items) + dashboard layout presets (`3dc2db4`) are both progressive-disclosure wins; settings sprawl is now consolidated and dashboard density is now a choice |
| Key terms unexplained inline | 🟡 Medium | "Rollover budget," "savings bundle," "advance tracking" |
| No sample data | 🟡 Medium | Can't see what a "working" app looks like |
| No authentication layer documented | 🟡 Medium | Security risk if exposed to the internet |
| ~~Currency hardcoded to VND (₫)~~ | ✅ Resolved (`ad7686d`) | Settings now offers a VND/USD/EUR **display** picker (cosmetic — symbol/placement only, no conversion of stored values). Adoption-limiting *display* friction is gone; true multi-currency accounting remains out of scope (see Scope Boundaries) |

---

## Proposed Solutions (Ordered by Value)

### Solution 1 — `docker-compose.yml` (Unblocks Self-Hosters) ~1 hour

**Create:** `docker-compose.yml` at the project root. The minimal setup is just the `app` service. Optional workers are commented out — progressive disclosure for self-hosters.

```yaml
services:
  app:
    image: ghcr.io/thevivotran/carange:latest
    ports:
      - "6868:6868"
    volumes:
      - carange_data:/data
    environment:
      - DATABASE_URL=sqlite:////data/carange.db
      - UPLOAD_DIR=/data/uploads
      # --- Optional: Telegram push notifications ---
      # - TELEGRAM_BOT_TOKEN=
      # - TELEGRAM_CHAT_ID=
      # --- Optional: AI budget insights (requires self-hosted LLM) ---
      # - OLLAMA_URL=http://your-llm-server:8000
    restart: unless-stopped

  # Optional OCR worker — uncomment to enable screenshot import
  # ocr_worker:
  #   image: ghcr.io/thevivotran/carange-ocr-worker:latest
  #   volumes:
  #     - carange_data:/data
  #   environment:
  #     - DATABASE_URL=sqlite:////data/carange.db
  #     - UPLOAD_DIR=/data/uploads
  #   restart: unless-stopped

  # Optional Email worker — uncomment to enable bank email ingestion
  # email_worker:
  #   image: ghcr.io/thevivotran/carange-email-worker:latest
  #   volumes:
  #     - carange_data:/data
  #   environment:
  #     - DATABASE_URL=sqlite:////data/carange.db
  #     - IMAP_HOST=imap.gmail.com
  #     - IMAP_USER=
  #     - IMAP_PASSWORD=   # Gmail App Password (requires 2FA enabled)
  #   restart: unless-stopped

volumes:
  carange_data:
```

Also create `.env.example` documenting all environment variables with descriptions.

---

### Solution 2 — Welcome Banner (Highest UX Impact) ~2.5 hours

**Detect first-time users** using the existing `settings` table (`app/services/settings_service.py` already has `get_setting()` / `set_setting()`). Store key `onboarding_complete = "true"` once dismissed.

#### Files to modify

**`main.py:131-140`** — pass `show_onboarding` to dashboard template:
```python
from app.services.settings_service import get_setting

@app.get("/", response_class=HTMLResponse)
async def dashboard_page(request: Request, db: Session = Depends(get_db)):
    data = get_dashboard_page_data(db)
    show_onboarding = get_setting(db, "onboarding_complete", "false") != "true"
    return templates.TemplateResponse(request, "dashboard.html", {
        **data,
        "show_onboarding": show_onboarding,
        "active_menu": "dashboard",
    })
```

**`app/routers/fragments/dashboard.py`** — add dismiss endpoint (router already imports `set_setting`):
```python
@router.post("/onboarding/dismiss")
def dismiss_onboarding(db: Session = Depends(get_db)):
    set_setting(db, "onboarding_complete", "true")
    return HTMLResponse("", headers={
        "HX-Trigger": '{"showToast": {"message": "Welcome! Start by adding your first transaction.", "type": "success"}}'
    })
```

**Create:** `app/templates/partials/onboarding/_welcome_banner.html`
- Tailwind card: `bg-blue-50 border border-blue-200 rounded-2xl p-6 mb-6`
- Headline: "Welcome to Carange — Your family's self-hosted finance tracker"
- 4-step "Getting Started" checklist:
  - Step 1: Add first income → `onclick="openGlobalAddModal()"`
  - Step 2: Review categories → CTA: `/categories`
  - Step 3: Set a budget → CTA: `/budget`
  - Step 4: Track savings → CTA: `/savings`
- Dismiss button: `hx-post="/fragments/dashboard/onboarding/dismiss"` + `hx-swap="outerHTML"`

**`app/templates/dashboard.html`** — inject at top of page content:
```html
{% if show_onboarding %}
{% include "partials/onboarding/_welcome_banner.html" %}
{% endif %}
```

---

### Solution 3 — AI Prompt Language Fix ~30 min

**File:** `app/services/insight_service.py:35-50`

Replace the hardcoded Vietnamese `_SYSTEM_WEEKLY` and `_SYSTEM_BUDGET` with English defaults:

```python
_SYSTEM_WEEKLY = (
    "You are a personal finance analyst. Analyze the weekly spending data and provide "
    "concise, actionable insights with specific numbers. Use English. "
    "No markdown, no emoji. Only cite figures from the provided data — do not round or estimate."
)

_SYSTEM_BUDGET = (
    "You are a personal finance advisor tracking a real-time monthly budget. "
    "Evaluate the current month's budget status and give specific, measurable advice. "
    "Use English. No markdown, no emoji. "
    "Only cite figures from the provided data — do not round or estimate."
)
```

---

### Solution 4 — Empty State Improvements ~1.5 hours

The reusable `app/templates/partials/_empty_state.html` partial exists but is underused on the dashboard. Wire it into the three most visible empty branches:

**`app/templates/dashboard.html`:**
- "No budget configured" branch → empty state card with CTA to `/budget`
- "No transactions" branch in Recent Transactions → empty state with quick-add modal trigger

**`app/templates/budget/index.html`** — empty allocations state:
> "Your 16 categories are ready. Click Edit Budgets to set monthly limits."

**`app/templates/savings/list.html`** — empty bundles state:
> "Track fixed deposits and savings goals. Create your first bundle to see projected growth."

---

### Solution 5 — Self-Hosting README ~1 hour

Add a clear self-hosting section to `README.md`:

```markdown
## Self-Hosting

### Quick Start (5 minutes)
1. Copy `docker-compose.yml` from this repo
2. `docker compose up -d`
3. Open `http://localhost:6868`

### Optional Features

| Feature | What to configure |
|---------|------------------|
| Screenshot import (OCR) | Uncomment `ocr_worker` in docker-compose.yml |
| Bank email import | Uncomment `email_worker` + set `IMAP_*` variables |
| AI budget insights | Set `OLLAMA_URL` to your LLM server |
| Push notifications | Set `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` |

### Security Note
This app has **no authentication**. Run it:
- On a local network only, OR
- Behind a VPN (WireGuard, Tailscale), OR
- Behind a reverse proxy with auth (Nginx + htpasswd, Authelia, etc.)

**Never expose port 6868 directly to the internet.**
```

---

## What NOT to Build (Scope Boundaries)

- **No multi-step forced wizard** — the dismissible banner is sufficient; don't block app use
- **No sample/demo data** — synthetic transactions in a finance app create "is this real?" confusion; 16 seeded categories are enough
- **No authentication system** — document the LAN/VPN constraint instead
- ~~**No currency configurability** — VND/₫ is embedded across 30+ templates; this is a larger separate refactor~~ — **Superseded:** a cosmetic display-currency picker shipped in `ad7686d` (VND/USD/EUR symbol & placement via a cached `currency_format` service + Jinja filters/globals). The boundary that still holds is **no currency *conversion*** — stored amounts stay in VND; multi-currency accounting (FX rates, mixed-currency reports) remains a larger separate effort and correctly out of scope
- **No full i18n system** — the AI prompt fix targets the immediate blocker for non-Vietnamese users only
- **No sidebar restructuring** — the current 3-group navigation (TODAY / PLANNING / SETTINGS) is already well-organized

---

## Self-Hosting Complexity Summary

| Setup tier | Requirements | Time | Complexity |
|-----------|-------------|------|------------|
| **Minimal** (Dashboard + manual entry) | `docker compose up` | ~5 min | ⭐ Very easy |
| **+ Import** (OCR from screenshots) | Uncomment `ocr_worker` | +10 min | ⭐⭐ Easy |
| **+ Notifications** | Telegram bot token + chat ID | +5 min | ⭐⭐ Easy |
| **+ Email ingestion** | Gmail App Password (2FA required) | +15 min | ⭐⭐⭐ Moderate |
| **+ AI Insights** | Self-hosted vLLM server (GPU required) | +60 min | ⭐⭐⭐⭐ Advanced |

---

## Implementation Order

| Priority | Solution | Est. Time | Delivers |
|----------|----------|-----------|---------|
| 1 | Rename dev compose + create self-host `docker-compose.yml` (SQLite) and `docker-compose.pg.yml` | 45m | Families can actually run the app, with a clear SQLite vs. PostgreSQL choice |
| 2 | Welcome banner | 2.5h | New users know where to start |
| 3 | AI prompt language fix | 30m | Non-Vietnamese users can use Pulse |
| 4 | Empty state improvements | 1.5h | Reduces confusion after banner dismissed |
| 5 | README + `.env.example` (document both SQLite and PostgreSQL paths) | 1h | Lowers self-hosting barrier |

**Total estimated effort: ~6.5 hours** (unchanged — less compose work, slightly more README work)

---

## Verification Checklist

- [ ] `docker compose up` from clean directory (SQLite path) → app starts, DB seeds 16 categories
- [ ] `docker compose -f docker-compose.pg.yml up` from clean directory (PostgreSQL path) → app starts, migrations run, DB seeds 16 categories
- [ ] First visit to `/` → welcome banner visible with 4 steps
- [ ] Click "Got it" → banner disappears (HTMX swap), success toast fires
- [ ] Reload → banner does not return
- [ ] Reset `onboarding_complete` in SQLite → banner reappears
- [ ] Visit `/budget` with no data → empty state with CTA visible
- [ ] Visit `/savings` with no data → empty state with CTA visible
- [ ] Configure `OLLAMA_URL` → Pulse AI section returns English insights
- [ ] Settings → switch dashboard layout to "Simple" → dashboard collapses to ~6 core cards (Health KPIs, Net Worth, Safety Score, Alerts, Recent Transactions)
- [ ] Settings → switch display currency to USD/EUR → amounts re-render with `$`/`€` symbol and correct placement everywhere (dashboard, transactions, budget, savings, projects, Pulse), stored values unchanged
- [ ] `make pre-push` passes (lint + audit + test + test-pg)
