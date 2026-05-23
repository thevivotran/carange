from fastapi import FastAPI, Request, Depends
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.gzip import GZipMiddleware
from sqlalchemy.orm import Session
from contextlib import asynccontextmanager
from calendar import monthrange

# Load environment variables
from dotenv import load_dotenv

load_dotenv()

from app.models.database import create_tables, get_db, SessionLocal
from app.models.database import Category, TransactionType
from app.routers import transactions, categories, savings, projects, dashboard, templates as templates_router
from app.routers import assets
from app.routers import notes
from app.routers import budget
from app.routers import import_jobs
from app.routers.dashboard import get_dashboard_page_data


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_tables()
    seed_default_categories()
    yield


# Create FastAPI app
app = FastAPI(title="Carange - Family Finance Tracker", lifespan=lifespan)

app.add_middleware(GZipMiddleware, minimum_size=1000)

# Mount static files
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Templates
templates = Jinja2Templates(directory="app/templates")


def seed_default_categories():
    db = SessionLocal()
    try:
        # Check if categories exist
        if db.query(Category).count() == 0:
            default_categories = [
                # Expense categories
                Category(name="Food & Dining", type=TransactionType.EXPENSE, color="#EF4444", icon="utensils"),
                Category(name="Transportation", type=TransactionType.EXPENSE, color="#F59E0B", icon="car"),
                Category(name="Shopping", type=TransactionType.EXPENSE, color="#EC4899", icon="shopping-bag"),
                Category(name="Entertainment", type=TransactionType.EXPENSE, color="#8B5CF6", icon="film"),
                Category(name="Utilities", type=TransactionType.EXPENSE, color="#6366F1", icon="bolt"),
                Category(name="Healthcare", type=TransactionType.EXPENSE, color="#10B981", icon="heartbeat"),
                Category(name="Education", type=TransactionType.EXPENSE, color="#3B82F6", icon="graduation-cap"),
                Category(name="Housing", type=TransactionType.EXPENSE, color="#78350F", icon="home"),
                Category(name="Insurance", type=TransactionType.EXPENSE, color="#F97316", icon="shield-alt"),
                Category(name="Others", type=TransactionType.EXPENSE, color="#6B7280", icon="ellipsis-h"),
                # Income categories
                Category(name="Salary", type=TransactionType.INCOME, color="#10B981", icon="money-bill-wave"),
                Category(name="Bonus", type=TransactionType.INCOME, color="#34D399", icon="gift"),
                Category(name="Investment", type=TransactionType.INCOME, color="#3B82F6", icon="chart-line"),
                Category(name="Freelance", type=TransactionType.INCOME, color="#8B5CF6", icon="laptop-code"),
                Category(name="Rental", type=TransactionType.INCOME, color="#F59E0B", icon="building"),
                Category(name="Others", type=TransactionType.INCOME, color="#6B7280", icon="ellipsis-h"),
            ]
            db.add_all(default_categories)
            db.commit()
    finally:
        db.close()


# Include routers
app.include_router(dashboard.router, prefix="/api")
app.include_router(transactions.router, prefix="/api/transactions")
app.include_router(categories.router, prefix="/api/categories")
app.include_router(savings.router, prefix="/api/savings")
app.include_router(projects.router, prefix="/api/projects")
app.include_router(templates_router.router, prefix="/api/templates")
app.include_router(assets.router, prefix="/api/assets")
app.include_router(notes.router, prefix="/api/notes")
app.include_router(budget.router, prefix="/api/budget")
app.include_router(import_jobs.router, prefix="/api/import")


@app.get("/health")
async def health():
    return JSONResponse({"status": "ok"})


# Main routes for HTML pages
@app.get("/", response_class=HTMLResponse)
async def dashboard_page(request: Request, db: Session = Depends(get_db)):
    data = get_dashboard_page_data(db)
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "active_menu": "dashboard",
            **data,
        },
    )


@app.get("/transactions", response_class=HTMLResponse)
async def transactions_page(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(request, "transactions/list.html", {"active_menu": "transactions"})


@app.get("/categories", response_class=HTMLResponse)
async def categories_page(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(request, "categories/list.html", {"active_menu": "categories"})


@app.get("/savings", response_class=HTMLResponse)
async def savings_page(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(request, "savings/list.html", {"active_menu": "savings"})


@app.get("/assets", response_class=HTMLResponse)
async def assets_page(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(request, "assets/list.html", {"active_menu": "assets"})


@app.get("/projects", response_class=HTMLResponse)
async def projects_page(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(request, "projects/list.html", {"active_menu": "projects"})


@app.get("/templates", response_class=HTMLResponse)
async def templates_page(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(request, "templates/list.html", {"active_menu": "templates"})


@app.get("/notes", response_class=HTMLResponse)
async def notes_page(request: Request):
    return templates.TemplateResponse(request, "notes/list.html", {"active_menu": "notes"})


@app.get("/budget", response_class=HTMLResponse)
async def budget_page(request: Request):
    return templates.TemplateResponse(request, "budget/index.html", {"active_menu": "budget"})


@app.get("/import", response_class=HTMLResponse)
async def import_page(request: Request):
    return templates.TemplateResponse(request, "import/upload.html", {"active_menu": "import"})


@app.get("/sw.js")
async def service_worker():
    return FileResponse(
        "app/static/sw.js", media_type="application/javascript", headers={"Service-Worker-Allowed": "/"}
    )


@app.get("/pulse", response_class=HTMLResponse)
async def pulse_page(request: Request, db: Session = Depends(get_db)):
    data = get_dashboard_page_data(db)
    summary = data["summary"]
    today = data["today"]

    _, last_day = monthrange(today.year, today.month)
    days_remaining = last_day - today.day
    day_pct = round(today.day / last_day * 100)

    adh = summary.get("budget_adherence_pct")
    net = summary.get("net_this_month", 0)
    income = summary.get("total_income", 0)
    over_count = summary.get("budget_over_count", 0)

    if income == 0:
        pulse_level = "amber"
        pulse_label = "No Income Yet"
        pulse_message = "No income recorded yet this month."
    elif net < 0:
        pulse_level = "red"
        pulse_label = "Over Spending"
        pulse_message = "Spending exceeds income this month — review expenses."
    elif adh is None:
        pulse_level = "amber"
        pulse_label = "No Budget Set"
        pulse_message = "Set a monthly budget to track spending health."
    elif adh >= 75:
        pulse_level = "green"
        pulse_label = "On Track"
        pulse_message = f"Great momentum — {adh}% of budget categories are on target."
    elif adh >= 50:
        cat_word = "category" if over_count == 1 else "categories"
        pulse_level = "amber"
        pulse_label = "Watch It"
        pulse_message = f"{over_count} {cat_word} over budget — review before month end."
    else:
        cat_word = "category" if over_count == 1 else "categories"
        pulse_level = "red"
        pulse_label = "Over Budget"
        pulse_message = f"{over_count} {cat_word} are over budget this month."

    check_income = summary["total_income"] > 0
    check_bds = summary["monthly_bds"] > 0
    check_tk = summary["monthly_tiet_kiem"] >= 20_000_000
    check_net = summary["net_this_month"] > 0
    ss_score = sum([check_income, check_bds, check_tk, check_net])

    return templates.TemplateResponse(
        request,
        "pulse/index.html",
        {
            "active_menu": "pulse",
            "summary": summary,
            "today": today,
            "days_remaining": days_remaining,
            "day_pct": day_pct,
            "last_day": last_day,
            "pulse_level": pulse_level,
            "pulse_label": pulse_label,
            "pulse_message": pulse_message,
            "check_income": check_income,
            "check_bds": check_bds,
            "check_tk": check_tk,
            "check_net": check_net,
            "ss_score": ss_score,
            "recent_transactions": data["recent_transactions"],
            "alert_over_budget": data["alert_over_budget"],
        },
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=6868)
