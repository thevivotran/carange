from fastapi import FastAPI, Request, Depends
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from contextlib import asynccontextmanager
import os

# Load environment variables
from dotenv import load_dotenv
load_dotenv()

from app.models.database import create_tables, get_db, SessionLocal
from app.models.database import Category, TransactionType
from app.routers import transactions, categories, savings, projects, dashboard, templates as templates_router
from app.routers import assets
from app.routers import notes
from app.routers.dashboard import get_dashboard_page_data

@asynccontextmanager
async def lifespan(app: FastAPI):
    create_tables()
    seed_default_categories()
    yield

# Create FastAPI app
app = FastAPI(title="Carange - Family Finance Tracker", lifespan=lifespan)

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

# Main routes for HTML pages
@app.get("/", response_class=HTMLResponse)
async def dashboard_page(request: Request, db: Session = Depends(get_db)):
    data = get_dashboard_page_data(db)
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "active_menu": "dashboard",
        **data,
    })

@app.get("/transactions", response_class=HTMLResponse)
async def transactions_page(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse("transactions/list.html", {
        "request": request,
        "active_menu": "transactions"
    })

@app.get("/categories", response_class=HTMLResponse)
async def categories_page(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse("categories/list.html", {
        "request": request,
        "active_menu": "categories"
    })

@app.get("/savings", response_class=HTMLResponse)
async def savings_page(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse("savings/list.html", {
        "request": request,
        "active_menu": "savings"
    })

@app.get("/projects", response_class=HTMLResponse)
async def projects_page(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse("projects/list.html", {
        "request": request,
        "active_menu": "projects"
    })

@app.get("/templates", response_class=HTMLResponse)
async def templates_page(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse("templates/list.html", {
        "request": request,
        "active_menu": "templates"
    })

@app.get("/notes", response_class=HTMLResponse)
async def notes_page(request: Request):
    return templates.TemplateResponse("notes/list.html", {
        "request": request,
        "active_menu": "notes"
    })

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=6868)