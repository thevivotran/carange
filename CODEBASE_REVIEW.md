# Carange — Whole-Codebase Review & Improvement Roadmap

## Context

A comprehensive audit of the carange family-finance app (FastAPI + SQLite + Jinja2/HTMX/
Alpine/Tailwind) across three axes: **performance**, **UX/UI**, and **feature completeness**.
The goal is a prioritized, grounded list of what to update/edit to make the app better.

Findings are verified against the code (file paths + line numbers cited). The app is already
mature — 16 features wired end-to-end, 31 test modules (~8.2k LOC), soft-delete + audit
logging throughout. This review focuses on the gaps that remain.

**Scope:** Authentication / multi-user is **out of scope** — single-user homelab deployment.
Noted as a deployment risk only (see Appendix).

---

## A. Performance

Ordered by ROI (impact ÷ effort).

### A1. Missing DB indexes on hot-path filter columns — HIGH / LOW effort
`app/models/database.py` defines indexes for `Transaction.date`, `(type,date)`, and
`category_id` (lines 197–198) but **not** for columns filtered on nearly every query:
- `Transaction.deleted_at` — filtered in almost every query (`deleted_at.is_(None)`).
- `Transaction.import_job_id` — filtered in `import_jobs.py`, `transaction_service.py`.
- `Transaction.needs_review` — review/import workflows.
- `Transaction.is_savings_related` — dashboard aggregates.
- `SavingsBundle.status`, `FinancialProject.status` — dashboard/list filters.

**Fix:** add `Index(...)` entries in the respective `__table_args__`. Because the app calls
`create_tables()` on startup (no Alembic migrations exist despite `alembic` being in
`requirements.txt`), new indexes apply to fresh DBs automatically; for the existing
`carange.db` add a one-off `CREATE INDEX IF NOT EXISTS` migration script or manual step.

### A2. Dashboard recomputes everything on every load — HIGH / MEDIUM effort
`app/services/dashboard_service.py` (783 LOC) has **no caching** (verified: no `lru_cache`/
`@cache`). Every dashboard hit recomputes 6-month trends, category aggregates, budget rows
(`compute_budget_rows()`), net-worth, and FI metrics. Acceptable now, painful past ~10k txns.

**Fix options (pick one):**
- Short in-memory TTL cache (e.g. 60–300s) keyed by `(year_month, data_version)`, invalidated
  on transaction/budget writes. No new infra.
- Hardcoded category-ID lookups (`"Tiết kiệm"`, `"Bất động sản"` at dashboard_service.py
  ~38–51) should be resolved once and cached, not re-queried per request.

### A3. Synchronous Telegram ping blocks transaction creation — MEDIUM / LOW effort
`app/services/transaction_service.py:149–153` calls `_tg.send_transaction_ping(db_tx)`
**synchronously inside the request path**. If Telegram is slow/down, every create/import
stalls. **Fix:** fire-and-forget via FastAPI `BackgroundTasks` or `asyncio.create_task`
(already graceful on exception, just needs to be off the critical path).

### A4. Python-side filtering that should be SQL — MEDIUM / LOW effort
- `import_jobs.py:144–148` loads all txns for a job then filters/counts in Python. Replace
  with `func.count(...).filter(...).scalar()`.
- `OtherAsset` loaded with `.all()` (dashboard_service.py ~335) — fine at current scale, but
  prefer a `func.sum` aggregate for net-worth contribution.

### A5. Payee regex matching is an in-memory O(payees × patterns) loop — MEDIUM / MEDIUM effort
`app/services/rules_service.py:28–40` loads **all** payees, JSON-decodes `alias_patterns`,
and runs `re.search` per pattern per transaction. Fine for dozens of payees; degrades import
throughput at hundreds+. **Fix:** pre-compile + cache compiled patterns at process start
(invalidate on payee write); longer-term consider SQLite FTS.

### A6. No pagination on a few list endpoints — LOW / LOW effort
`categories.py`, `payees.py`, `rules.py` return `.all()` with no skip/limit. Safe at MVP
scale (<1k rows); add `skip`/`limit` params for consistency with `transactions`.

### A7. HTMX fragment load waterfalls — LOW / LOW effort
Pages like transactions trigger summary + list fragments sequentially on `load`. Minor; could
combine or use `hx-select` to fetch once.

---

## B. UX / UI

### B1. Empty loading/empty-state partials are 0 bytes — HIGH / LOW effort
`app/templates/partials/_empty_state.html` and `_loading_row.html` are **both empty**
(verified `wc -c` = 0) yet conceptually referenced. Lists/tables show blank gaps before HTMX
swaps and bare text on empty. **Fix:** implement a reusable empty-state (icon + message + CTA)
and a skeleton loading row, then include them across transactions, savings, projects, budget,
import, review.

### B2. No field-level form validation feedback — MEDIUM / MEDIUM effort
Every modal form (Add Transaction, Rules, Payees, Budget, Project, Savings) surfaces errors
only as a generic toast (`err.detail || 'Failed…'`). Gaps:
- Rules: `value` not validated against operator (`range` needs 2 numbers; `regex` not test-
  compiled) — bad regex can reach the backend.
- Payees: alias pattern regex not validated before save.
- Savings: no check that `future_amount > initial_deposit`; date ranges unchecked.
- No `disabled` submit-while-pending; no inline `<span class="error">` per field.

**Fix:** lightweight client validation + inline error spans; disable submit during request.
Keep DOM via `createElement`/`textContent` (the innerHTML security hook will reject otherwise).

### B3. Chart fill colors not theme-aware + no destroy on re-render — LOW / LOW effort
`dashboard.html` correctly themes grid/tick/legend via `_isDark()` (lines 536–539) but the
**bar/area fill colors are hardcoded RGBA** (566, 574, 584, 659, 669, 681) — they don't flip
on dark-mode toggle without reload. Also no `chart.destroy()` before re-init on HTMX swap →
potential leaked Chart instances. **Fix:** derive fills from `_isDark()`; destroy prior chart.

### B4. Accessibility gaps — MEDIUM / MEDIUM effort
Only ~30 aria attributes across 54 templates. Missing: modal focus-trap + `role="dialog"`,
`aria-label` on icon-only buttons (e.g. review "approve all"), table header `scope`, label
association on color/icon pickers, `aria-live` for dynamic badges/toasts. `tap-highlight:
transparent` plus no visible focus ring hurts keyboard nav.

### B5. Mixed-language label — LOW / LOW effort
UI is ~99% English but `partials/dashboard/_kpi_cards.html:25` shows `"Tiết kiệm ÷ Income"`.
Replace with "Savings ÷ Income" for consistency.

### B6. Styling inconsistency — LOW / LOW effort
Button padding scales vary (`px-3 py-2` … `px-6 py-3`), mixed shadow tiers, and ~167 lines of
`!important` dark-mode overrides in `base.html` (a Tailwind-CDN limitation). Optional: adopt a
small set of component classes; longer-term move off the Tailwind CDN to a build step
(`static/dist/` already exists).

### B7. Verify "Export CSV" wiring — LOW / LOW effort
`transactions/list.html` shows an Export CSV button; its JS handler wasn't located during the
review. Confirm it's wired (or remove the dead control).

---

## C. Feature Completeness

### C1. Recurring template scheduler — the biggest functional gap — HIGH / MEDIUM-HIGH effort
`TransactionTemplate` has `cadence`, `next_run_at`, `last_run_at`, `auto_approve`, `lead_days`
in the schema, but **nothing executes them** — templates are only a manual "Use template"
prefill. No `apscheduler` in `requirements.txt`. **To complete:** a daily background job that
finds templates due (`next_run_at <= today`), auto-creates transactions (respecting
`auto_approve`, `lead_days`), and advances `next_run_at`. Deployed as systemd already, so a
scheduler thread or a separate worker (like `email_worker/`) both fit.

### C2. Pulse insights are partial — MEDIUM / MEDIUM effort
`fragments/pulse.py` does a week-on-week digest + optional LLM budget commentary (Qwen3.5-9B
via Ollama, gracefully degrades when `OLLAMA_URL` unset). Missing the "intelligence" half:
forecasting, anomaly detection beyond fixed thresholds, proactive alerts.

### C3. Legacy project milestone/contribution cleanup — MEDIUM / LOW-MEDIUM effort
`ProjectPayment` superseded the older milestone/contribution model, but legacy code/columns
linger. Removing them reduces confusion and dead paths.

### C4. CSV import error reporting & batch safety — MEDIUM / MEDIUM effort
`transaction_service.parse_csv_*` returns a 400 on `ValueError` but doesn't report per-row
validation detail, and a mid-batch constraint violation can roll back the whole import
silently. **Fix:** collect row-level errors, return a summary (imported / skipped / failed +
reasons), commit in a savepoint per row or chunk.

### C5. OCR captures totals only — LOW / MEDIUM effort
Shopee/Timo/Grab/generic parsers extract totals, not line items. Receipt line-item extraction
+ content-hash dedup (beyond the existing SHA-256 image-hash) would improve fidelity. Nice-to-
have.

### C6. Google Sheets sync — documented but absent — LOW / decide-to-cut
`AGENTS.md` lists "Google Sheets sync" but there's **no code and no google-auth deps**. Either
implement (OAuth + Sheets API) or **remove it from AGENTS.md** so docs match reality. Given
email + OCR + CSV already cover ingestion, recommend cutting it from docs unless wanted.

### C7. No Alembic migrations despite the dependency — MEDIUM / MEDIUM effort
`alembic` is in `requirements.txt` but there are zero migrations; schema is created via
`create_tables()`. Any column/index change to an existing `carange.db` currently needs manual
SQL. Introducing a baseline Alembic migration makes A1/C3 safe on the live DB.

---

## Suggested sequencing

**Tier 1 — quick, high-ROI (½–1 day):** A1 indexes, A3 async Telegram, A4 SQL counts,
B1 empty/loading partials, B3 chart fills/destroy, B5 language label, C6 docs fix.

**Tier 2 — medium (2–4 days):** A2 dashboard cache, A5 payee pattern cache, B2 form
validation, B4 accessibility pass, C4 CSV error reporting.

**Tier 3 — larger features:** C1 recurring scheduler, C7 Alembic baseline, C3 legacy
cleanup, C2 Pulse intelligence.

---

## Verification (for the implementation sessions)

- Always run `make all` in `carange_app/carange/` before pushing. Fix everything it reports.
- Run the app: `cd carange_app/carange && python main.py` → http://localhost:6868; smoke-test
  dashboard, transactions, import, review.
- Query the DB via `.venv/bin/python3 -c "from app.models.database import ..."` (no `sqlite3`
  CLI). Confirm new indexes with `PRAGMA index_list('transactions')`.
- Add/extend tests alongside each change (existing suite: 31 modules, in-memory SQLite via
  `conftest.py`). Verify recurring-scheduler logic with a frozen-clock test.
- Watch the innerHTML security hook: DOM building must use `createElement`/`textContent`/
  `appendChild` only.

---

## Appendix — out-of-scope notes

- **Authentication / multi-user:** intentionally excluded. The app is fully public at `/` with
  no User model or session layer. Acceptable only because it sits on a private homelab network.
  Flag if the deployment surface ever changes.
