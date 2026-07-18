# Plan: Unified Money-Flow Ledger & Reconciliation Guards

## Problem

`cash_on_hand` reports **−69,041,538 VND** (extremely negative) while the original
Excel `Tracker!J1` (`SUM(Thu) − SUM(Chi)`) meant "net family money that touched
wife's spendable account" and was **positive**.

Root cause is structural, not a formula typo:

- The app tracks **four pots** of family money — spendable cash, liquid savings
  (`savings_bundles`), assets/real-estate (`other_assets`), projects
  (`project_payments`) — but classifies money movement with only
  `transaction.type = income|expense`.
- Depositing into a savings bundle / buying real estate is recorded as a
  **single-leg `expense`**. That same VND is **subtracted from `cash_on_hand`**
  *and* **stored as a pot balance that is added back into `net_worth`**. Net worth
  nets out; `cash_on_hand` alone is dragged negative by every deposit.
- Net capital moved into savings/RE/investment ≈ **378.6M**, which is exactly the
  gap between the raw −69M and the true family surplus of **+309.6M**.
- Classification is fragmented across 8 overlapping flags that no single function
  reads consistently (`type`, `is_savings_related`, `savings_bundle_id`,
  `project_id`, `category.kpi_role`, `is_savings_category`, `is_wealth_building`,
  `spend_nature`). Proof of existing drift: category **Tiết kiệm** has 426M of
  expense but only 305M is flagged `is_savings_related`.
- Additional correctness bug: `net_worth` adds `SavingsBundle.future_amount`
  (projected target) while the cash side subtracted actual deposits — the two
  sides of net worth measure different things.

**Every new pot/feature adds another flag and another way to diverge.** The fix
must be a single classification source of truth plus an enforced invariant.

## Decisions (approved)

1. **Two co-equal KPIs**, so −69M is never mistaken for "who owes whom":
   - **Net family surplus** = `external_income − external_expense` (transfers
     excluded). Positive ⇒ wife holds family surplus (wife owes family);
     negative ⇒ family owes wife. ≈ **+309.6M**.
   - **Liquid cash** = spendable balance after transfers = raw
     `Σincome − Σexpense`. ≈ **−69M** (its negativity is itself a data signal —
     see Phase 4).
2. **Scope: centralized ledger + guards** (no full double-entry rewrite, no schema
   overhaul of the transaction table).

## Target model

Every transaction is a directed edge between CASH and one counterparty pot:

```
Pot ∈ { EXTERNAL, LIQUID_SAVINGS, REAL_ESTATE, INVESTMENT, PROJECT }

EXTERNAL, in   → external_income   (salary, gift, cashback)
EXTERNAL, out  → external_expense  (food, bills, wedding)
<internal pot> → TRANSFER          (net-zero to net worth)
```

Invariants the guards enforce:

```
external_income − external_expense        ==  Δnet_worth        (transfers cancel)
net_worth  ==  liquid_cash + savings + assets + projects         (stock == Σ pots)
Σincome − Σexpense (raw)  ==  liquid_cash                         (bridge)
net_family_surplus  ==  external_income − external_expense
```

## Phases / tasks

> **Status (2026-07-18):** Phase 1 DONE & verified against production (100% cov,
> 24 tests). Verified figures: `liquid_cash = −69,041,538`, `net_family_surplus =
> +342,307,614`; reconciliation/bridge identities hold to the cent. Precedence
> decision: **category `kpi_role` beats FK links** (real_estate/liquid_savings win
> over project_id/savings_bundle_id) so the real-estate KPI bucket stays accurate.
> **Phase 2+3 DONE & verified (2026-07-18):** consumers (dashboard/forecast) wired
> onto ledger; two co-equal KPIs (Family Surplus / Liquid Cash) added to
> `_kpi_cards.html`; `net_worth` VALUE unchanged; reconciliation + completeness
> guard tests added (`tests/test_ledger_reconciliation.py`) incl. two deliberate-
> break cases. `make test`: 1211 passed, 95.54% cov, ledger.py 100%. Not committed.
> INVESTMENT still keyed off category name "Đầu tư" (no role yet) — formalize in
> Phase 4. Loose end for Phase 5: `app/routers/transactions.py:447` still computes
> `cash_on_hand` inline (all-time income−expense) — migrate to `ledger.liquid_cash`.
> **Next: Phase 4 (data backfill) — needs user sign-off on per-bundle reconciliation.**

### Phase 1 — `app/services/ledger.py` (single source of truth)
- `Pot` enum + `classify(txn_or_row) -> (Direction, Pot)` deriving the pot from
  category role + FK links (`savings_bundle_id`, `project_id`, `kpi_role`,
  `is_savings_category`). Consolidate the 8 flags behind this one function.
- Canonical aggregate helpers (ORM + matview-compatible): `external_income`,
  `external_expense`, `net_family_surplus`, `liquid_cash`, `pot_balance(pot)`,
  `net_worth`. These become the **only** implementations.
- Keep `get_cash_on_hand` name as a thin alias during migration; forecast start
  balance uses `liquid_cash`.
- **Done when:** module imports cleanly; unit tests cover each classification case.

### Phase 2 — Refactor consumers to the ledger
- `dashboard_service.py`: replace the inline `cash_on_hand` (2 sites: matview +
  live path) and the inline `net_worth` assembly with ledger calls. Expose both
  new KPIs in the dashboard dict.
- `forecast_service.py`, `savings_service.py`: import from ledger; delete
  duplicate math.
- Fix the `future_amount` vs actual-deposit inconsistency in `net_worth`
  (use a single consistent measure for the savings pot).
- **Done when:** no `income − expense` or `net_worth =` arithmetic exists outside
  `ledger.py`; dashboard renders both KPIs.

### Phase 3 — Regression guards (the anti-drift tripwire)
- **Reconciliation test** (`tests/test_ledger_reconciliation.py`): asserts all four
  invariants against a seeded DB and against the real snapshot.
- **Classifier-completeness test**: every `kpi_role` value, every pot-linked FK,
  every category flag maps to a `Pot`; a new unmapped role/table fails the test.
- **Done when:** both tests pass and fail correctly when a pot is left unwired
  (add a deliberate-break case).

### Phase 4 — Data backfill & reconciliation

**Savings gap — investigated 2026-07-18 (findings):**
- Excel `Saving` sheet has 19 bundles (Carange 1–19, 359.66M lifetime deposits,
  374.70M received). carange-fin has 14: 10 migrated (Carange 8,10,12–19), **9
  never migrated** (Carange 1–7,9,11 = 124.3M, exist only inside aggregate monthly
  "Tiết kiệm" expense rows), and 4 created post-export (Carange 20–23 = 140M).
- **Cutover ~Nov 2025:** pre-cutover bundles (8/10/12) have NO linked deposit txn —
  their outflow is the 11 unlinked monthly "Tiết kiệm" expenses (120.79M, the
  426M-vs-305M gap). Post-cutover bundles (13–23) each have a linked "Initial
  deposit" expense (305.36M).
- Bug: Carange 8 (35M) has no deposit record anywhere but its 35M return IS booked
  as income → inflates cash ~35M. Carange 10 (25.64M) & 12 (10.25M) returns are
  unlinked/unflagged plain income.
- net_worth uses `SavingsBundle.future_amount` (active 296.1M incl. ~21M UNEARNED
  interest); `current_amount` is a static mirror of `initial_deposit`.
- Net "Tiết kiệm" drag on cash_on_hand = 426.15M − 101.26M = **324.90M** (dominant
  part of the −69M).

**Reconciliation findings (2026-07-18, verified vs production):**
- ROOT of surplus overstatement: there are TWO "Tiết kiệm" categories — expense
  (id 28, `kpi_role='liquid_savings'`) and income (id 38, `kpi_role=None`). The
  income one lacking the role lets non-bundle-linked savings RETURNS leak into
  EXTERNAL income. Exactly 2 txns leak: id 323 (25,642,740, "Carange 10") + id 457
  (10,254,301, "Carange 12") = 35,897,041. Fixing → `net_family_surplus`
  +342,307,614 → **+306,410,573**.
- `net_worth` savings term uses `future_amount` (296.1M incl ~21M unearned
  interest) → switch to `current_amount` (275M). net_worth −21.1M.
- Carange 8 (bundle id 23): 35M DEPOSIT is unrecorded (its 35M return IS booked)
  → `liquid_cash` currently ~35M too HIGH.
- 9 un-migrated early bundles (Carange 1–7,9,11): deposits sit in legacy monthly
  Tiết kiệm expenses, returns appear UNRECORDED → `liquid_cash` ~124M too LOW.
- Bundle ids: Carange 10=2, Carange 12=3, Carange 8=23.

**KEY INSIGHT:** `net_family_surplus` (who-owes-whom) is immune to bundle recording
gaps (all savings flows excluded once classified). `liquid_cash` (raw spendable) is
highly sensitive to them. So the surplus is cleanly fixable; liquid_cash needs a
per-bundle leg reconciliation.

**Split decision (user, 2026-07-18): apply clean fixes now, review liquid_cash next.**

CLEAN FIXES — IN PROGRESS on Sonnet worker (code + Alembic rev 0035, not committed):
1. income "Tiết kiệm" (id 38) → `kpi_role='liquid_savings'` (match by name+type).
2. "Đầu tư" (id 36/37) → `kpi_role='investment'`; ledger uses role as primary,
   name-match kept as fallback; 'investment' added to VALID_KPI_ROLES.
3. link orphan payouts id 323→bundle 2, id 457→bundle 3 (+ is_savings_related).
4. net_worth savings term `future_amount` → `current_amount` (active).
5. `transactions.py:447` inline cash_on_hand → `ledger.liquid_cash` (Phase-5 loose
   end folded in).
Update reconciliation test identity (B) for current_amount.

liquid_cash reconciliation — REVIEWED & CLOSED (user decision 2026-07-18): DO NOT FIX.
Full 23-bundle recording-state table built. Findings:
- Group A (active C13-15,19-23, 275M) + Group B (completed C10,16,17,18): correctly
  recorded, no action.
- Group C (deposit missing): C8 (35M deposit unrecorded, return booked) + C12
  (~8.2M partial) → liquid_cash ~+43M too HIGH. EXACT/fixable.
- Group D (un-migrated early bundles C1-7,9,11): pre-Nov-2025 "cutover" era recorded
  savings as MONTHLY AGGREGATE Tiết kiệm expenses (odd amounts, don't map 1:1 to
  round bundle deposits); returns never booked → liquid_cash ~−90-125M too LOW.
  UNTRACEABLE (user: legacy txns merged multiple prior savings + top-ups).
- Net: true liquid_cash ≈ −20M to +15M vs displayed −69M, but not precisely
  recoverable.
DECISION: book nothing. Fixing Group C alone would push liquid_cash to ~−112M
(further from truth) while Group D stays unbooked. Accept liquid_cash as approximate
for the legacy era; rely on `net_family_surplus` (+306,410,573) as the trustworthy
"who owes whom" KPI. `liquid_cash` is immune... no — SURPLUS is immune to these gaps;
liquid_cash is not, hence it stays approximate. Phase 3 guard tests protect FUTURE
data regardless.

**Phase 5 DONE (2026-07-18):** "adding a new money pot" contract added to CONTRIBUTING.md
(commit d78922a) + the loaded monorepo CLAUDE.md gotchas (untracked, not a git repo).

**STATUS: COMPLETE.** PR #82 (Phases 1-5). Only remaining optional polish: none required.

### Phase 5 — Guardrails for future features
- CLAUDE.md gotcha: "Adding a new money pot" checklist (register in `Pot`, tag
  funding transactions as transfers, the reconciliation test enforces it).
- Note in the `new-route` skill.
- **Done when:** docs updated; checklist references the completeness test.

## Out of scope
- Full double-entry accounting with explicit account rows and two-legged transfers.
- Transaction-table schema overhaul.

## Verification
- `make pre-push` (lint + audit + test 95% coverage + PostgreSQL tests).
- Dashboard shows both KPIs with correct signs; forecast start balance unchanged
  in meaning; net worth unchanged in value (invariant preserved).
