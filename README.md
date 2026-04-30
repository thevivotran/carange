# Carange — Family Finance Tracker

A self-hosted web app for tracking a family's finances: daily spending, savings, investment projects, assets, budget allocation, and notes. Built with FastAPI and SQLite, designed for LAN access from any device.

## Features

### 1. Dashboard
- Monthly income / expense overview with net balance
- Expense breakdown by category (chart)
- Net worth summary: savings bundles + financial projects + other assets
- Recent transactions and upcoming savings maturities
- Project funding progress

### 2. Transactions
- Log income and expense transactions with date, category, description, payment method
- Filter by month, type, category, and keyword search
- Edit and delete with undo support
- Link transactions to savings bundles or financial projects
- CSV export
- Quick-entry via Templates

### 3. Categories
- Custom expense and income categories with color and icon
- Add, edit, deactivate categories
- Vietnamese category set used by default

### 4. Templates
- Save recurring transactions as templates (fixed monthly expenses, etc.)
- One-click "Use Template" to pre-fill a new transaction

### 5. Savings Bundles
- Track fixed deposits, recurring deposits, and savings goals
- Record bank, interest rate, maturity date, current and future value
- Log contributions and link bundles to financial projects
- Mark as completed — automatically creates an income transaction for the matured amount

### 6. Financial Projects
- Track multi-step financial goals (Real Estate, Investment, Vehicle, Education, Vacation, Custom)
- Set priority, deadline, target amount, and milestones
- Log contributions (manual or from a savings bundle)
- Payment schedule with due dates and paid/pending status
- Progress tracking with percentage completion

### 7. Other Assets
- Record holdings in foreign currency, gold, and other assets
- Track quantity, unit, purchase cost (VND), and current estimated value
- Contributes to net worth on the Dashboard

### 8. Budget Allocation
- Envelope-style monthly budgets per expense category
- **Rollover balances:** unspent budget carries forward; overspending rolls as a deficit
- Baseline starts from May 2026 — all history computed from that point
- Month navigation to view any past or future month
- Edit Budgets modal with "Effective from" month picker (set future budgets without touching the current month)
- Add new categories to the budget at any time; remove categories to stop tracking them
- Per-category progress bar with colour coding (green / amber / red)
- Summary bar showing total monthly spend vs. total allocation

### 9. Notes
- Two-panel editor (list + content) for free-form notes
- Types: General, Money Owed
- Auto-save with 800 ms debounce
- Filter notes by type
- Includes a pinned "Hướng dẫn phân loại giao dịch" (transaction categorisation guide)

## Technical Stack

- **Backend:** FastAPI (Python 3.12+)
- **Database:** SQLite (`carange.db`, auto-created on first run via `Base.metadata.create_all`)
- **ORM:** SQLAlchemy (no Alembic — schema changes via `create_all`)
- **Schemas:** Pydantic v2
- **Frontend:** Jinja2 templates, Tailwind CSS (CDN), Chart.js (CDN), Font Awesome (CDN)
- **PWA:** manifest + service worker for mobile home-screen install
- **Currency:** Vietnamese Dong (VND)

## Installation

```bash
git clone git@github.com:thevivotran/carange.git
cd carange
uv sync          # or: pip install -r requirements.txt
```

## Running

```bash
# Quickest
bash scripts/run.sh

# Manual
source .venv/bin/activate
python main.py

# With uvicorn (hot-reload for dev)
uvicorn main:app --reload --host 0.0.0.0 --port 6868
```

Available at:
- Local: http://localhost:6868
- Network: http://YOUR_LOCAL_IP:6868

## Deployment (systemd autostart)

```bash
sudo bash scripts/setup-autostart.sh
```

Manage the service:

```bash
sudo systemctl start|stop|restart|status carange
```

## Access from Other Devices

1. Find your local IP: `ip addr`
2. Allow port 6868 through the firewall if needed
3. Open `http://YOUR_IP:6868` on any device on the same network

## API Reference

| Router | Prefix | Key endpoints |
|--------|--------|---------------|
| Dashboard | `/api` | `GET /summary`, `/monthly-trend`, `/expense-by-category` |
| Transactions | `/api/transactions` | CRUD + `GET /export` |
| Categories | `/api/categories` | CRUD |
| Templates | `/api/templates` | CRUD |
| Savings | `/api/savings` | CRUD + `POST /{id}/contribute`, `POST /{id}/mark-completed` |
| Projects | `/api/projects` | CRUD + milestones, contributions, payments |
| Assets | `/api/assets` | CRUD |
| Budget | `/api/budget` | `GET /{ym}/rows`, `POST /`, `PUT /{id}`, `DELETE /category/{id}`, `DELETE /{id}` |
| Notes | `/api/notes` | CRUD |

## License

Personal project — family use only.
