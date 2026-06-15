from fastapi import FastAPI, Request, Depends
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.gzip import GZipMiddleware
from sqlalchemy.orm import Session
from contextlib import asynccontextmanager
from datetime import date

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
from app.routers import review as review_router
from app.routers import rules as rules_router
from app.routers import payees as payees_router
from app.routers import settings as settings_router
from app.routers import profiles as profiles_router
from app.routers import forecast as forecast_router
from app.routers.dashboard import get_dashboard_page_data
from app.middleware import ProfileMiddleware
from app.services.settings_service import get_setting
from app.routers.fragments import transactions as frag_transactions
from app.routers.fragments import dashboard as frag_dashboard
from app.routers.fragments import budget as frag_budget
from app.routers.fragments import savings as frag_savings
from app.routers.fragments import projects as frag_projects
from app.routers.fragments import assets as frag_assets
from app.routers.fragments import categories as frag_categories
from app.routers.fragments import templates_page as frag_templates
from app.routers.fragments import import_page as frag_import
from app.routers.fragments import pulse as frag_pulse
from app.routers.fragments import review as frag_review
from app.routers.fragments import rules as frag_rules
from app.routers.fragments import payees as frag_payees


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_tables()
    seed_default_categories()
    from app.services.scheduler import start_scheduler

    start_scheduler()
    yield


# Create FastAPI app
app = FastAPI(title="Carange - Family Finance Tracker", lifespan=lifespan)

app.add_middleware(GZipMiddleware, minimum_size=1000)
# Resolves the household profile cookie and gates non-public routes
app.add_middleware(ProfileMiddleware)

# Mount static files
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Templates
templates = Jinja2Templates(directory="app/templates")
from app.routers.fragments._helpers import _decimal_safe_tojson
from app.services.currency_format import register as register_currency_filters, inject_currency
from app.services.dashboard_layout import inject_nav_items

templates.env.filters["tojson"] = _decimal_safe_tojson
register_currency_filters(templates.env)
templates.context_processors.append(inject_currency)
templates.context_processors.append(inject_nav_items)


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

            # Fresh install — start new users on the decluttered "Simple" dashboard
            # rather than the 20+ card "Full" view; they can switch in Settings.
            from app.services.settings_service import set_setting

            set_setting(db, "dashboard_layout", "simple")
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
app.include_router(review_router.router, prefix="/api/review")
app.include_router(rules_router.router, prefix="/api/rules")
app.include_router(payees_router.router, prefix="/api/payees")
app.include_router(settings_router.router, prefix="/settings")
app.include_router(profiles_router.router, prefix="/profiles")
app.include_router(forecast_router.router, prefix="/api/forecast")

# Fragment routers (HTML partials for HTMX)
app.include_router(frag_transactions.router, prefix="/fragments/transactions", tags=["fragments"])
app.include_router(frag_dashboard.router, prefix="/fragments/dashboard", tags=["fragments"])
app.include_router(frag_budget.router, prefix="/fragments/budget", tags=["fragments"])
app.include_router(frag_savings.router, prefix="/fragments/savings", tags=["fragments"])
app.include_router(frag_projects.router, prefix="/fragments/projects", tags=["fragments"])
app.include_router(frag_assets.router, prefix="/fragments/assets", tags=["fragments"])
app.include_router(frag_categories.router, prefix="/fragments/categories", tags=["fragments"])
app.include_router(frag_templates.router, prefix="/fragments/templates", tags=["fragments"])
app.include_router(frag_import.router, prefix="/fragments/import", tags=["fragments"])
app.include_router(frag_pulse.router, prefix="/fragments/pulse", tags=["fragments"])
app.include_router(frag_review.router, prefix="/fragments/review", tags=["fragments"])
app.include_router(frag_rules.router, prefix="/fragments/rules", tags=["fragments"])
app.include_router(frag_payees.router, prefix="/fragments/payees", tags=["fragments"])


@app.get("/health")
async def health():
    return JSONResponse({"status": "ok"})


# Main routes for HTML pages
@app.get("/", response_class=HTMLResponse)
async def dashboard_page(request: Request, db: Session = Depends(get_db)):
    from app.routers.settings import _ordinal_suffix
    from app.services.fiscal_period import get_month_start_day

    data = get_dashboard_page_data(db)
    show_onboarding = get_setting(db, "onboarding_complete", "false") != "true"
    month_start_day = get_month_start_day(db)
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "active_menu": "dashboard",
            "visible_sections": request.state.visible_sections,
            "show_onboarding": show_onboarding,
            "month_start_day": month_start_day,
            "month_start_day_label": f"{month_start_day}{_ordinal_suffix(month_start_day)}",
            **data,
        },
    )


@app.get("/transactions", response_class=HTMLResponse)
async def transactions_page(request: Request, db: Session = Depends(get_db)):
    from app.services.fiscal_period import get_month_start_day

    return templates.TemplateResponse(
        request,
        "transactions/list.html",
        {"active_menu": "transactions", "month_start_day": get_month_start_day(db)},
    )


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


@app.get("/forecast", response_class=HTMLResponse)
async def forecast_page(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(request, "forecast/index.html", {"active_menu": "forecast"})


@app.get("/templates", response_class=HTMLResponse)
async def templates_page(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(request, "templates/list.html", {"active_menu": "templates"})


@app.get("/notes", response_class=HTMLResponse)
async def notes_page(request: Request):
    return templates.TemplateResponse(request, "notes/list.html", {"active_menu": "notes"})


@app.get("/budget", response_class=HTMLResponse)
async def budget_page(request: Request, db: Session = Depends(get_db)):
    from app.services.fiscal_period import get_month_start_day

    return templates.TemplateResponse(
        request,
        "budget/index.html",
        {"active_menu": "budget", "month_start_day": get_month_start_day(db)},
    )


@app.get("/import", response_class=HTMLResponse)
async def import_page(request: Request):
    return templates.TemplateResponse(request, "import/upload.html", {"active_menu": "import"})


@app.get("/sw.js")
async def service_worker():
    return FileResponse(
        "app/static/sw.js", media_type="application/javascript", headers={"Service-Worker-Allowed": "/"}
    )


@app.get("/review", response_class=HTMLResponse)
async def review_page(request: Request):
    return templates.TemplateResponse(request, "review/index.html", {"active_menu": "review"})


@app.get("/rules", response_class=HTMLResponse)
async def rules_page(request: Request, db: Session = Depends(get_db)):
    from app.models.database import Category

    categories = db.query(Category).filter(Category.is_active == True).order_by(Category.name).all()
    return templates.TemplateResponse(request, "rules/index.html", {"active_menu": "rules", "categories": categories})


@app.get("/payees", response_class=HTMLResponse)
async def payees_page(request: Request, db: Session = Depends(get_db)):
    from app.models.database import Category

    categories = db.query(Category).filter(Category.is_active == True).order_by(Category.name).all()
    return templates.TemplateResponse(request, "payees/index.html", {"active_menu": "payees", "categories": categories})


@app.get("/pulse", response_class=HTMLResponse)
async def pulse_page(request: Request, db: Session = Depends(get_db)):
    from app.services.fiscal_period import (
        current_period_label,
        current_period_ym,
        day_index_in_period,
        days_in_period,
        get_month_start_day,
    )

    data = get_dashboard_page_data(db)
    summary = data["summary"]
    today = data["today"]

    month_start_day = get_month_start_day(db)
    day_index = day_index_in_period(today, month_start_day)
    last_day = days_in_period(current_period_label(today, month_start_day), month_start_day)
    days_remaining = last_day - day_index
    day_pct = round(day_index / last_day * 100)
    fiscal_year, fiscal_month = current_period_ym(today, month_start_day)
    fiscal_month_name = date(fiscal_year, fiscal_month, 1).strftime("%B")

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
    check_tk = summary["liquid_savings_rate"] >= summary["savings_target_pct"]
    check_net = summary["net_this_month"] > 0
    ss_score = sum([check_income, check_bds, check_tk, check_net])

    return templates.TemplateResponse(
        request,
        "pulse/index.html",
        {
            "active_menu": "pulse",
            "summary": summary,
            "today": today,
            "day_index": day_index,
            "days_remaining": days_remaining,
            "day_pct": day_pct,
            "last_day": last_day,
            "fiscal_month_name": fiscal_month_name,
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
