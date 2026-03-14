# Carange - Family Finance Tracker

A web application built with Python FastAPI for tracking family finances, including daily transactions, savings bundles, and financial projects.

## Features

### 1. Transaction Tracking
- Daily expense and income logging
- Custom categories (add, edit, remove)
- Monthly summaries and analytics
- CSV export functionality
- Quick transaction entry via templates

### 2. Savings Bundles
- Track multiple savings accounts/goals
- Monitor progress towards targets
- Interest rate and maturity date tracking
- Contribution history
- Link savings to projects

### 3. Financial Projects
- Create and track financial goals (Real Estate, Investment, Education, etc.)
- Set milestones for each project
- Track contributions and progress
- Priority levels and deadlines
- Link to savings bundles

### 4. Dashboard
- Monthly income/expense overview
- Net balance calculation
- Category breakdown with charts
- Recent transactions
- Upcoming savings maturities
- Projects summary

### 5. Templates
- Create templates for recurring transactions
- One-click "Use Template" to quickly add transactions
- Edit and manage templates easily

## Technical Stack

- **Backend**: FastAPI (Python 3.12+)
- **Database**: SQLite (`carange.db`, auto-created on first run)
- **ORM**: SQLAlchemy + Alembic
- **Frontend**: Jinja2 templates, Tailwind CSS, Chart.js, FontAwesome
- **Dependency management**: [uv](https://docs.astral.sh/uv/)

## Installation

```bash
git clone git@github.com:thevivotran/carange.git
cd carange
uv sync
```

## Running the Application

### Quickest way
```bash
bash scripts/run.sh
```

### Manual
```bash
source .venv/bin/activate
python main.py
```

### With uvicorn directly
```bash
uvicorn main:app --reload --host 0.0.0.0 --port 6868
```

The app will be available at:
- Local: http://localhost:6868
- Network: http://YOUR_LOCAL_IP:6868

## Deployment (systemd autostart)

To run Carange automatically on system boot:

```bash
sudo bash scripts/setup-autostart.sh
```

This installs and enables a systemd service. Manage it with:

```bash
sudo systemctl start carange
sudo systemctl stop carange
sudo systemctl restart carange
sudo systemctl status carange
```

## Access from Other Devices

1. Find your local IP: `ip addr` or `ifconfig`
2. Ensure port 6868 is allowed through your firewall
3. Access from any device on the same network: `http://YOUR_IP:6868`

## Currency

The application uses **Vietnamese Dong (VND)** as the default currency.

## Mobile Support

Fully responsive — works on desktop, tablet, and mobile. Can be installed as a Progressive Web App (PWA) for quick access.

## API Endpoints

### Dashboard
- `GET /api/dashboard/summary`
- `GET /api/dashboard/monthly-trend`
- `GET /api/dashboard/expense-by-category`

### Transactions
- `GET /api/transactions/`
- `POST /api/transactions/`
- `PUT /api/transactions/{id}`
- `DELETE /api/transactions/{id}`

### Categories
- `GET /api/categories/`
- `POST /api/categories/`
- `PUT /api/categories/{id}`
- `DELETE /api/categories/{id}`

### Savings
- `GET /api/savings/`
- `POST /api/savings/`
- `PUT /api/savings/{id}`
- `POST /api/savings/{id}/contribute`
- `POST /api/savings/{id}/mark-completed`

### Projects
- `GET /api/projects/`
- `POST /api/projects/`
- `PUT /api/projects/{id}`
- `POST /api/projects/{id}/contribute`
- `GET /api/projects/{id}/milestones`
- `POST /api/projects/{id}/milestones`

### Templates
- `GET /api/templates/`
- `POST /api/templates/`
- `PUT /api/templates/{id}`
- `DELETE /api/templates/{id}`

## License

Personal project for family use.
