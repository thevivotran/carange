# carange — Product Improvement Plan

Generated: 2026-05-15

---

## Overview

This plan captures all product issues identified in the PM review of carange. Items are grouped by theme and ordered by priority within each group.

---

## Section 1: Remove Duplication

### 1.1 Delete Legacy Project System (Milestones + Contributions)
**Problem:** Two parallel systems exist for tracking project funding:
- Legacy: `ProjectMilestone` + `ProjectContribution` (with UI sections in project detail panel)
- Current: `ProjectPayment` (auto-creates transactions, supports recurring schedules, PAID/PENDING status)

Both are visible in the project detail panel, creating confusion about which is the canonical approach.

**Action:**
- Remove `/api/projects/{id}/milestones` endpoints (GET, POST, PATCH complete, DELETE)
- Remove `/api/projects/{id}/contributions` endpoints (GET, POST)
- Remove `POST /{id}/contribute` and `POST /{id}/link-savings/{savings_id}` if only used by legacy
- Drop `ProjectMilestone` and `ProjectContribution` DB tables after migration
- Remove milestone and contribution UI sections from `projects/list.html`

**Outcome:** Single, clean payment model. No ambiguity for users.

---

### 1.2 Consolidate Monthly Summary (Three Sources of Truth)
**Problem:** Income/expense/savings totals appear in three places:
- Dashboard: `/api/dashboard/summary` (KPI cards)
- Transactions page: `/api/transactions/stats/monthly-summary` (summary bar)
- Budget page: `/api/budget/{ym}/rows` (summary bar)

Month selectors are independent across pages, so figures can show different months simultaneously.

**Action:**
- Keep all three summary bars but ensure they all use a shared month state (see Section 2.3)
- Visually demote the Transactions summary bar — make it smaller/secondary, not a duplicate KPI grid
- Consider merging `/transactions/stats/monthly-summary` data into the dashboard API to reduce round-trips

**Outcome:** One source of truth per month; no contradictory figures across views.

---

### 1.3 Split Savings and Other Assets into Separate Pages
**Problem:** "Savings Bundles" (bank deposits, TDs) and "Other Assets" (gold, USD) share the Savings page under two tabs. These are fundamentally different:
- Savings: structured around interest rates, maturity dates, deposit cycles
- Assets: market-value tracking, gain/loss

**Action:**
- Create a standalone `/assets` page for Other Assets
- Move "Other Assets" tab out of `/savings`
- Add "Assets" to sidebar navigation (under Wealth Building alongside Savings)
- Update mobile bottom nav "More" sheet to include Assets

**Outcome:** Cleaner mental model. "Savings" means bank deposits only.

---

### 1.4 Fix Templates → Transaction Round-Trip Redirect
**Problem:** Using a template requires: Templates page → click Use → sessionStorage redirect → Transactions page → modal auto-opens. This is a 3-step flow for a 1-step action.

**Action:**
- Add a "Use Template" shortcut inside the quick-add transaction modal (dropdown or icon button)
- Load template list inline when the modal opens
- Remove the sessionStorage redirect pattern (`useTemplate` key)
- Keep the Templates page for CRUD management only

**Outcome:** Apply a template in one click from wherever the user already is.

---

## Section 2: UX Fixes

### 2.1 Surface Advances on the Dashboard
**Problem:** `is_advance` / `advance_settled` flags exist but are invisible. No dashboard alert, no count badge, no filter shortcut. A user only discovers an unsettled advance by clicking into a specific transaction row.

**Action:**
- Add an "Open Advances" alert card on the Dashboard (similar to savings maturity card)
- Show: count of unsettled advances + total amount owed
- Link directly to `/transactions?advance=unsettled`
- Add an "Advances" quick filter to the Transactions filter bar

**Outcome:** Family advance tracking becomes a first-class visible feature.

---

### 2.2 Show Budget Rollover Breakdown
**Problem:** The envelope rollover carries unspent budget forward, but the UI shows only one "available" number. Users can't tell how much is new allocation vs. carried-over credit.

**Action:**
- In each budget category row, show a breakdown tooltip or sub-label:
  `₫2M new + ₫3M rolled over = ₫5M available`
- Add a "rolled over" column to the Budget History modal
- Color-code rolled-over amounts distinctly (e.g., lighter shade)

**Outcome:** A hidden power feature becomes a visible reward for underspending.

---

### 2.3 Synchronize Month Selectors Across Pages
**Problem:** Dashboard and Budget each have independent month selectors. Navigating between pages resets to current month. Users analyzing a specific past month lose context on every page switch.

**Action:**
- Store the active month in the URL query param: `?month=2026-03`
- On page load, read `?month` and initialize the selector to that value
- All inter-page navigation links should propagate the current month param
- Fall back to current month if param is absent or invalid

**Outcome:** Analyzing March stays in March across Dashboard → Budget → Transactions.

---

### 2.4 Make Auto-Created Transactions Traceable
**Problem:** Two features silently create transactions:
- Marking a project payment PAID → creates an expense transaction
- Marking a savings bundle complete → creates an income transaction

These appear in the Transactions list with no indication of origin. Users see mystery entries.

**Action:**
- Add a `source` field to Transaction: `manual | project_payment | savings_maturity`
- Display a small badge in the transaction list row: e.g., "Auto • Project" or "Auto • Savings"
- Filter by source in the Advanced Filters panel
- On delete of an auto-transaction, warn: "This was auto-created by [Project/Savings]. Deleting it will not undo the source record."

**Outcome:** Full audit trail; users trust the data.

---

### 2.5 Improve Mobile "More" Navigation
**Problem:** The bottom nav "More" button leads to an implicit overflow menu. Savings and Projects — the two most important wealth-building features — are hidden behind it with no visual hint.

**Action:**
- Replace the generic "More" with a proper bottom sheet listing all hidden pages with icons:
  - Savings, Projects, Notes, Templates, Categories
- OR replace one of the existing 5 tabs with "Savings" or "Projects" (higher-value features than Budget or Txns as a standalone)
- Add descriptive labels to all bottom nav items (no abbreviations like "Txns")

**Outcome:** Core features reachable in one tap on mobile.

---

## Section 3: Information Architecture

### 3.1 Restructure Sidebar Navigation
**Problem:** "Daily Finance" mixes daily-use features (Transactions) with configuration tools (Categories, Templates). This creates navigation noise.

**Current:**
- Overview: Dashboard
- Daily Finance: Transactions, Budget, Categories, Templates
- Wealth Building: Savings, Projects
- Other: Notes

**Proposed:**
- **Today:** Dashboard, Transactions
- **Planning:** Budget, Savings, Projects, Assets *(new)*
- **Settings:** Categories, Templates
- **Notes** *(keep as standalone or integrate — see 3.2)*

**Action:** Reorganize sidebar links and section headers in `base.html`.

---

### 3.2 Integrate or Remove Notes
**Problem:** Notes (`/notes`) has no integration with any other entity. A `money_owed` note doesn't link to a transaction or contact. It's a disconnected sticky-note app.

**Decision required (choose one):**

**Option A — Integrate:**
- Allow notes to be attached to: Projects, Savings Bundles, Budget months
- Add a "Notes" count badge on entity cards
- Remove the standalone Notes page; surface notes inline on entity detail views

**Option B — Remove:**
- Export any existing notes to CSV
- Drop the Notes table, router, and template
- If free-text notes are needed, add a `notes` text field directly on the relevant entities

**Recommendation:** Option A if family uses notes actively; Option B if it's unused dead weight.

---

## Section 4: Data Integrity Guardrails

### 4.1 Duplicate Detection in CSV Bulk Upload
**Problem:** Re-importing the same CSV creates silent duplicate transactions. No deduplication logic exists.

**Action:**
- Before inserting, check for existing transaction with same (date, amount, type, category_id, description)
- Return a preview diff before committing: "X new, Y duplicates skipped, Z conflicts"
- Add a `--force` override for intentional re-imports

---

### 4.2 Category Merge on Delete
**Problem:** Category deletion is blocked if it has transactions (correct), but there's no way to consolidate two categories. Leads to category sprawl over time.

**Action:**
- On `DELETE /api/categories/{id}`, if category has transactions, offer a merge endpoint:
  `POST /api/categories/{id}/merge-into/{target_id}`
- Migrates all transactions to target category, then deletes source
- Add "Merge into..." option in the category delete confirmation modal

---

### 4.3 Replace Hardcoded Budget Baseline
**Problem:** Budget page has a hardcoded baseline of `2026-05`. Budget rows won't appear for months before this date. New users starting in a different month see an empty page with no explanation.

**Action:**
- Replace the hardcoded baseline with a dynamic value: earliest existing `BudgetAllocation.year_month`, or current month if none exist
- Add a first-run state: if no allocations exist, show a "Set up your first budget" prompt instead of an empty table

---

### 4.4 Warn Before Deleting Savings Bundle with Linked Transactions
**Problem:** Deleting a savings bundle unlinks but doesn't delete its transactions. The user may think they deleted everything; transactions remain as orphans.

**Action:**
- Before delete, count linked transactions
- If count > 0: show warning: "This bundle has {N} linked transactions. They will remain in your history but will no longer be associated with any savings bundle."
- Require explicit confirmation checkbox

---

## Priority Roadmap

| Priority | Item | Effort | Impact |
|----------|------|--------|--------|
| P0 | 1.1 Remove legacy milestones/contributions | Medium | High — removes dead code + confusion |
| P0 | 2.3 Unify month selector via URL param | Low | High — immediate cross-page consistency |
| P1 | 2.4 Traceable auto-created transactions | Low | High — data trust |
| P1 | 2.1 Surface advances on dashboard | Low | High — family use case |
| P1 | 4.3 Fix hardcoded budget baseline | Low | Medium — new user onboarding |
| P2 | 2.2 Budget rollover breakdown | Low | Medium — power feature visibility |
| P2 | 1.3 Split Savings / Assets pages | Medium | Medium — cleaner IA |
| P2 | 3.1 Restructure sidebar navigation | Low | Medium — discoverability |
| P2 | 4.1 CSV duplicate detection | Medium | Medium — data integrity |
| P3 | 1.4 Fix templates round-trip UX | Low | Low-Medium |
| P3 | 2.5 Improve mobile "More" nav | Medium | Medium |
| P3 | 1.2 Consolidate monthly summary | High | Medium |
| P3 | 3.2 Integrate or remove Notes | Medium | Low-Medium |
| P3 | 4.2 Category merge on delete | Medium | Low |
| P3 | 4.4 Savings bundle delete warning | Low | Low |

---

## Out of Scope (Future Considerations)

- Multi-user / family member separation (requires auth system redesign)
- Recurring transactions (beyond project payments)
- Bank reconciliation / statement matching
- Full data export (beyond CSV transactions)
- Audit log (who changed what, when)
- Push notifications for savings maturity / budget alerts
