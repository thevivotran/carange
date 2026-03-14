# Carange - Family Finance Tracker

A comprehensive web application built with Python FastAPI for tracking family finances, including daily transactions, savings bundles, and financial projects.

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
- Interest rate tracking
- Maturity date tracking
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
- Templates for regular expenses (rent, utilities, etc.)
- Edit and manage templates easily

## Technical Stack

- **Backend**: FastAPI (Python)
- **Database**: SQLite (file-based)
- **Frontend**: Server-side rendering with Jinja2 templates
- **Styling**: Tailwind CSS
- **Charts**: Chart.js
- **Icons**: FontAwesome

## Installation

1. Clone or download the project
2. Install dependencies:
```bash
cd carange
pip install -r requirements.txt
```

## Running the Application

### Development Mode
```bash
python main.py
```
Or:
```bash
uvicorn main:app --reload --host 0.0.0.0 --port 6868
```

### Production Mode (Local Network)
```bash
uvicorn main:app --host 0.0.0.0 --port 6868
```

The application will be accessible at:
- Local: http://localhost:6868
- Network: http://YOUR_LOCAL_IP:6868 (e.g., http://192.168.1.111:6868)

## Access from Other Devices

1. Find your computer's local IP address:
   - Windows: Open Command Prompt and run `ipconfig`
   - Mac/Linux: Open Terminal and run `ifconfig` or `ip addr`

2. Make sure your firewall allows connections on port 6868

3. Access from any device on the same network using: `http://YOUR_IP:6868`

## Database

The application uses SQLite with a file-based database (`carange.db`) that will be created automatically on first run.

### Default Categories

The application comes with pre-configured categories:

**Expense Categories:**
- Food & Dining
- Transportation
- Shopping
- Entertainment
- Utilities
- Healthcare
- Education
- Housing
- Insurance
- Others

**Income Categories:**
- Salary
- Bonus
- Investment
- Freelance
- Rental
- Others

## Backups

### Automatic Weekly Backups
The application includes a backup script (`backup.py`) that can be scheduled to run weekly.

**To set up weekly backups on Linux/Mac:**
1. Make the script executable:
```bash
chmod +x backup.py
```

2. Add to crontab to run every Sunday at midnight:
```bash
crontab -e
```

3. Add this line:
```
0 0 * * 0 cd /path/to/carange && /usr/bin/python3 backup.py
```

**To set up weekly backups on Windows:**
1. Open Task Scheduler
2. Create a new task to run weekly
3. Set the action to run `python backup.py` in the carange directory

### Manual Backup
```bash
python backup.py
```

Backups are stored in the `backups/` directory with timestamps. The system keeps the last 10 backups automatically.

## Currency

The application uses **Vietnamese Dong (VND)** as the default currency.

## Mobile Support

The application is fully responsive and works on:
- Desktop computers
- Tablets
- Mobile phones

It can be installed as a Progressive Web App (PWA) on mobile devices for quick access.

## Data Structure

### Transactions
- Date, Amount, Type (Income/Expense)
- Category, Description
- Payment method (default: Cash)
- Links to savings or projects (optional)

### Savings Bundles
- Name, Bank, Type (Fixed Deposit - default)
- Initial Deposit, Target Amount, Current Amount
- Interest Rate, Start Date, Maturity Date
- Status (Active/Completed)

### Financial Projects
- Name, Type (Real Estate/Investment/etc.)
- Target Amount, Current Amount
- Priority, Status, Deadline
- Milestones and Contributions

## API Endpoints

### Dashboard
- `GET /api/dashboard/summary` - Get summary statistics
- `GET /api/dashboard/monthly-trend` - Get monthly trend data
- `GET /api/dashboard/expense-by-category` - Get expense breakdown

### Transactions
- `GET /api/transactions/` - List all transactions
- `POST /api/transactions/` - Create new transaction
- `PUT /api/transactions/{id}` - Update transaction
- `DELETE /api/transactions/{id}` - Delete transaction

### Categories
- `GET /api/categories/` - List all categories
- `POST /api/categories/` - Create new category
- `PUT /api/categories/{id}` - Update category
- `DELETE /api/categories/{id}` - Delete category

### Savings
- `GET /api/savings/` - List all savings bundles
- `POST /api/savings/` - Create new savings bundle
- `PUT /api/savings/{id}` - Update savings bundle
- `POST /api/savings/{id}/contribute` - Add contribution
- `POST /api/savings/{id}/mark-completed` - Mark as completed

### Projects
- `GET /api/projects/` - List all projects
- `POST /api/projects/` - Create new project
- `PUT /api/projects/{id}` - Update project
- `POST /api/projects/{id}/contribute` - Add contribution
- `GET /api/projects/{id}/milestones` - Get milestones
- `POST /api/projects/{id}/milestones` - Add milestone

## License

This is a personal project for family use.

## Support

For issues or questions, please check the project documentation or contact the developer.