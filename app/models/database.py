from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, Date, Boolean, ForeignKey, Text, Enum
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from datetime import datetime, timezone
import enum

Base = declarative_base()

# Enums
class TransactionType(str, enum.Enum):
    EXPENSE = "expense"
    INCOME = "income"

class SavingsType(str, enum.Enum):
    FIXED_DEPOSIT = "fixed_deposit"
    RECURRING_DEPOSIT = "recurring_deposit"
    SAVINGS_GOAL = "savings_goal"

class SavingsStatus(str, enum.Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"

class ProjectType(str, enum.Enum):
    REAL_ESTATE = "real_estate"
    INVESTMENT = "investment"
    VEHICLE = "vehicle"
    EDUCATION = "education"
    VACATION = "vacation"
    CUSTOM = "custom"

class ProjectStatus(str, enum.Enum):
    PLANNING = "planning"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"

class Priority(str, enum.Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

class PaymentStatus(str, enum.Enum):
    PENDING = "pending"
    PAID    = "paid"

class AssetType(str, enum.Enum):
    CURRENCY = "currency"
    GOLD = "gold"
    OTHER = "other"

class Category(Base):
    __tablename__ = "categories"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    type = Column(Enum(TransactionType), nullable=False)
    color = Column(String(7), default="#3B82F6")
    icon = Column(String(50), default="circle")
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    
    transactions = relationship("Transaction", back_populates="category")

class Transaction(Base):
    __tablename__ = "transactions"
    
    id = Column(Integer, primary_key=True, index=True)
    date = Column(Date, nullable=False, index=True)
    amount = Column(Float, nullable=False)
    type = Column(Enum(TransactionType), nullable=False)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=False)
    description = Column(Text, nullable=True)
    payment_method = Column(String(50), default="cash")
    is_savings_related = Column(Boolean, default=False)
    savings_bundle_id = Column(Integer, ForeignKey("savings_bundles.id"), nullable=True)
    project_id = Column(Integer, ForeignKey("financial_projects.id"), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    
    category = relationship("Category", back_populates="transactions")
    savings_bundle = relationship("SavingsBundle", back_populates="transactions")
    project = relationship("FinancialProject", back_populates="transactions")

class SavingsBundle(Base):
    __tablename__ = "savings_bundles"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)
    bank_name = Column(String(100), nullable=False)
    type = Column(Enum(SavingsType), nullable=False)
    initial_deposit = Column(Float, nullable=False)  # Amount deposited
    current_amount = Column(Float, nullable=False)   # Current available balance
    future_amount = Column(Float, nullable=False)    # Amount at maturity (including interest)
    interest_rate = Column(Float, nullable=True)     # Annual interest rate
    start_date = Column(Date, nullable=False)
    maturity_date = Column(Date, nullable=True)
    status = Column(Enum(SavingsStatus), default=SavingsStatus.ACTIVE)
    notes = Column(Text, nullable=True)
    linked_project_id = Column(Integer, ForeignKey("financial_projects.id"), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    completed_at = Column(DateTime, nullable=True)
    
    transactions = relationship("Transaction", back_populates="savings_bundle")
    linked_project = relationship("FinancialProject", back_populates="linked_savings")
    contributions = relationship("ProjectContribution", back_populates="savings_bundle")

class FinancialProject(Base):
    __tablename__ = "financial_projects"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)
    type = Column(Enum(ProjectType), nullable=False)
    description = Column(Text, nullable=True)
    target_amount = Column(Float, nullable=False, default=0)
    current_amount = Column(Float, default=0)
    priority = Column(Enum(Priority), default=Priority.MEDIUM)
    status = Column(Enum(ProjectStatus), default=ProjectStatus.PLANNING)
    deadline = Column(Date, nullable=True)
    default_category_id = Column(Integer, ForeignKey("categories.id"), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    completed_at = Column(DateTime, nullable=True)

    transactions = relationship("Transaction", back_populates="project")
    linked_savings = relationship("SavingsBundle", back_populates="linked_project")
    milestones = relationship("ProjectMilestone", back_populates="project")
    contributions = relationship("ProjectContribution", back_populates="project")
    payments = relationship("ProjectPayment", back_populates="project", cascade="all, delete-orphan")

class ProjectMilestone(Base):
    __tablename__ = "project_milestones"
    
    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("financial_projects.id"), nullable=False)
    name = Column(String(200), nullable=False)
    target_amount = Column(Float, nullable=False)
    is_completed = Column(Boolean, default=False)
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    
    project = relationship("FinancialProject", back_populates="milestones")

class ProjectContribution(Base):
    __tablename__ = "project_contributions"
    
    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("financial_projects.id"), nullable=False)
    amount = Column(Float, nullable=False)
    date = Column(Date, nullable=False)
    source = Column(String(50), default="manual")
    savings_bundle_id = Column(Integer, ForeignKey("savings_bundles.id"), nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    
    project = relationship("FinancialProject", back_populates="contributions")
    savings_bundle = relationship("SavingsBundle", back_populates="contributions")

class ProjectPayment(Base):
    __tablename__ = "project_payments"

    id             = Column(Integer, primary_key=True, index=True)
    project_id     = Column(Integer, ForeignKey("financial_projects.id"), nullable=False)
    due_date       = Column(Date, nullable=True)
    amount         = Column(Float, nullable=False)
    status         = Column(Enum(PaymentStatus), default=PaymentStatus.PENDING, nullable=False)
    notes          = Column(Text, nullable=True)
    sort_order     = Column(Integer, default=0)
    transaction_id = Column(Integer, ForeignKey("transactions.id"), nullable=True)
    created_at     = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    project     = relationship("FinancialProject", back_populates="payments")
    transaction = relationship("Transaction")


class OtherAsset(Base):
    __tablename__ = "other_assets"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)
    asset_type = Column(Enum(AssetType), nullable=False)
    symbol = Column(String(20), nullable=True)           # e.g., USD, EUR, SJC
    quantity = Column(Float, nullable=False)              # amount held
    unit = Column(String(50), nullable=False)             # display unit, e.g., USD, tael, gram
    purchase_price_vnd = Column(Float, nullable=False)   # total VND cost basis
    current_value_vnd = Column(Float, nullable=False)    # current estimated VND value
    notes = Column(Text, nullable=True)
    acquired_date = Column(Date, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class TransactionTemplate(Base):
    __tablename__ = "transaction_templates"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)
    amount = Column(Float, nullable=False)
    type = Column(Enum(TransactionType), nullable=False)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=False)
    description = Column(Text, nullable=True)
    payment_method = Column(String(50), default="cash")
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    
    category = relationship("Category")

class Note(Base):
    __tablename__ = "notes"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(200), nullable=False)
    content = Column(Text, nullable=True)
    type = Column(String(50), nullable=True)  # e.g. 'money_owed', 'general'
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

# Database configuration
DATABASE_URL = "sqlite:///./carange.db"

engine = create_engine(
    DATABASE_URL, 
    connect_args={"check_same_thread": False}
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def create_tables():
    Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()