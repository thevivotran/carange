from pydantic import BaseModel, Field, field_validator
from datetime import date, datetime
from typing import Optional, List

# Python 3.12 evaluates annotated assignment values before annotation expressions in class bodies.
# When a field is named `date` with default `None`, `date = None` is stored first, causing
# `Optional[date]` to resolve as `NoneType`. Using an alias avoids this name collision.
_Date = date
from app.models.database import TransactionType, SavingsType, SavingsStatus, ProjectType, ProjectStatus, Priority, PaymentStatus, AssetType

# Category Schemas
class CategoryBase(BaseModel):
    name: str
    type: TransactionType
    color: str = "#3B82F6"
    icon: str = "circle"
    is_active: bool = True

class CategoryCreate(CategoryBase):
    pass

class CategoryUpdate(CategoryBase):
    pass

class Category(CategoryBase):
    id: int
    created_at: datetime
    transaction_count: int = 0

    class Config:
        from_attributes = True

# Savings Schemas
class SavingsBundleBase(BaseModel):
    name: str
    bank_name: str
    type: SavingsType
    initial_deposit: float = Field(gt=0, description="Initial deposit must be greater than 0")  # Amount deposited
    current_amount: Optional[float] = Field(default=0, ge=0, description="Current available balance")   # Current available balance
    future_amount: float = Field(gt=0, description="Future amount must be greater than 0")    # Amount at maturity (including interest)
    interest_rate: Optional[float] = Field(None, ge=0, le=100, description="Interest rate must be between 0 and 100")  # Annual interest rate
    start_date: date
    maturity_date: Optional[date] = None
    notes: Optional[str] = None
    
    @field_validator('initial_deposit', 'future_amount')
    @classmethod
    def validate_positive_amounts(cls, v):
        if v <= 0:
            raise ValueError('Amount must be greater than 0')
        return v
    
    @field_validator('current_amount')
    @classmethod
    def validate_non_negative_amount(cls, v):
        if v < 0:
            raise ValueError('Current amount cannot be negative')
        return v
    
    @field_validator('maturity_date')
    @classmethod
    def validate_maturity_date(cls, v, info):
        if v is not None:
            values = info.data
            start_date = values.get('start_date')
            if start_date and v < start_date:
                raise ValueError('Maturity date must be after start date')
        return v

class SavingsBundleCreate(SavingsBundleBase):
    linked_project_id: Optional[int] = None

class SavingsBundleUpdate(BaseModel):
    name: Optional[str] = None
    bank_name: Optional[str] = None
    initial_deposit: Optional[float] = None
    current_amount: Optional[float] = Field(None, ge=0)
    future_amount: Optional[float] = None
    interest_rate: Optional[float] = Field(None, ge=0, le=100)
    start_date: Optional[date] = None
    maturity_date: Optional[date] = None
    status: Optional[SavingsStatus] = None
    notes: Optional[str] = None
    linked_project_id: Optional[int] = None

    @field_validator('initial_deposit', 'future_amount')
    @classmethod
    def validate_positive_amounts(cls, v):
        if v is not None and v <= 0:
            raise ValueError('Amount must be greater than 0')
        return v

    @field_validator('maturity_date')
    @classmethod
    def validate_maturity_date(cls, v, info):
        if v is not None:
            start_date = info.data.get('start_date')
            if start_date and v < start_date:
                raise ValueError('Maturity date must be after start date')
        return v

class SavingsBundle(SavingsBundleBase):
    id: int
    status: SavingsStatus
    linked_project_id: Optional[int]
    created_at: datetime
    completed_at: Optional[datetime]
    
    class Config:
        from_attributes = True

# Transaction Schemas
class TransactionBase(BaseModel):
    date: date
    amount: float = Field(gt=0, description="Amount must be greater than 0")
    type: TransactionType
    category_id: int
    description: Optional[str] = None
    payment_method: str = "cash"
    
    @field_validator('amount')
    @classmethod
    def validate_amount(cls, v):
        if v <= 0:
            raise ValueError('Amount must be greater than 0')
        return v

class TransactionCreate(TransactionBase):
    is_savings_related: bool = False
    savings_bundle_id: Optional[int] = None
    project_id: Optional[int] = None
    savings_bundle: Optional[SavingsBundleCreate] = None  # For creating new savings bundle with transaction

class TransactionUpdate(BaseModel):
    date: Optional[_Date] = None
    amount: Optional[float] = None
    type: Optional[TransactionType] = None
    category_id: Optional[int] = None
    description: Optional[str] = None
    payment_method: Optional[str] = None
    is_savings_related: Optional[bool] = None
    savings_bundle_id: Optional[int] = None
    project_id: Optional[int] = None

class Transaction(TransactionBase):
    id: int
    is_savings_related: bool
    savings_bundle_id: Optional[int]
    project_id: Optional[int]
    created_at: datetime
    updated_at: datetime
    category: Category
    
    class Config:
        from_attributes = True

# Payment Schemas
class ProjectPaymentBase(BaseModel):
    due_date:   Optional[date] = None
    amount:     float = Field(gt=0)
    status:     PaymentStatus = PaymentStatus.PENDING
    notes:      Optional[str] = None
    sort_order: int = 0

class ProjectPaymentCreate(ProjectPaymentBase):
    pass  # project_id from URL path

class ProjectPaymentUpdate(BaseModel):
    due_date:     Optional[date] = None
    amount:       Optional[float] = Field(None, gt=0)
    status:       Optional[PaymentStatus] = None
    notes:        Optional[str] = None
    sort_order:   Optional[int] = None
    category_id:  Optional[int] = None   # triggers auto-transaction creation when marking paid
    payment_date: Optional[date] = None  # actual paid date for the transaction record

class ProjectPayment(ProjectPaymentBase):
    id:             int
    project_id:     int
    transaction_id: Optional[int] = None
    created_at:     datetime

    class Config:
        from_attributes = True


# Project Schemas
class ProjectMilestoneBase(BaseModel):
    name: str
    target_amount: float
    is_completed: bool = False

class ProjectMilestoneCreate(ProjectMilestoneBase):
    project_id: int

class ProjectMilestone(ProjectMilestoneBase):
    id: int
    project_id: int
    completed_at: Optional[datetime]
    created_at: datetime
    
    class Config:
        from_attributes = True

class ProjectContributionBase(BaseModel):
    amount: float = Field(gt=0, description="Contribution amount must be greater than 0")
    date: date
    source: str = "manual"
    savings_bundle_id: Optional[int] = None
    notes: Optional[str] = None
    
    @field_validator('amount')
    @classmethod
    def validate_contribution_amount(cls, v):
        if v <= 0:
            raise ValueError('Contribution amount must be greater than 0')
        return v

class ProjectContributionCreate(ProjectContributionBase):
    project_id: int

class ProjectContribution(ProjectContributionBase):
    id: int
    project_id: int
    created_at: datetime
    
    class Config:
        from_attributes = True

class FinancialProjectBase(BaseModel):
    name: str
    type: ProjectType
    description: Optional[str] = None
    priority: Priority = Priority.MEDIUM
    deadline: Optional[date] = None
    default_category_id: Optional[int] = None

class FinancialProjectCreate(FinancialProjectBase):
    pass

class FinancialProjectUpdate(BaseModel):
    name:                Optional[str] = None
    description:         Optional[str] = None
    priority:            Optional[Priority] = None
    status:              Optional[ProjectStatus] = None
    deadline:            Optional[date] = None
    default_category_id: Optional[int] = None

class FinancialProject(FinancialProjectBase):
    id:                  int
    target_amount:       float
    current_amount:      float
    status:              ProjectStatus
    created_at:          datetime
    completed_at:        Optional[datetime]
    progress_percentage: float = 0.0
    milestones:          List[ProjectMilestone] = []
    contributions:       List[ProjectContribution] = []
    linked_savings:      List[SavingsBundle] = []
    payments:            List[ProjectPayment] = []

    class Config:
        from_attributes = True

# Other Asset Schemas
class OtherAssetBase(BaseModel):
    name: str
    asset_type: AssetType
    symbol: Optional[str] = None
    quantity: float = Field(gt=0, description="Quantity must be greater than 0")
    unit: str
    purchase_price_vnd: float = Field(ge=0, description="Purchase price cannot be negative")
    current_value_vnd: float = Field(ge=0, description="Current value cannot be negative")
    notes: Optional[str] = None
    acquired_date: Optional[date] = None

class OtherAssetCreate(OtherAssetBase):
    pass

class OtherAssetUpdate(BaseModel):
    name: Optional[str] = None
    asset_type: Optional[AssetType] = None
    symbol: Optional[str] = None
    quantity: Optional[float] = Field(None, gt=0)
    unit: Optional[str] = None
    purchase_price_vnd: Optional[float] = Field(None, ge=0)
    current_value_vnd: Optional[float] = Field(None, ge=0)
    notes: Optional[str] = None
    acquired_date: Optional[date] = None

class OtherAsset(OtherAssetBase):
    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# Transaction Template Schemas
class TransactionTemplateBase(BaseModel):
    name: str
    amount: float = Field(gt=0, description="Amount must be greater than 0")
    type: TransactionType
    category_id: int
    description: Optional[str] = None
    payment_method: str = "cash"
    is_active: bool = True
    
    @field_validator('amount')
    @classmethod
    def validate_amount(cls, v):
        if v <= 0:
            raise ValueError('Amount must be greater than 0')
        return v

class TransactionTemplateCreate(TransactionTemplateBase):
    pass

class TransactionTemplateUpdate(BaseModel):
    name: Optional[str] = None
    amount: Optional[float] = None
    type: Optional[TransactionType] = None
    category_id: Optional[int] = None
    description: Optional[str] = None
    payment_method: Optional[str] = None
    is_active: Optional[bool] = None

class TransactionTemplate(TransactionTemplateBase):
    id: int
    created_at: datetime
    updated_at: datetime
    category: Category
    
    class Config:
        from_attributes = True

# Dashboard Schemas
class DashboardSummary(BaseModel):
    total_income_month: float
    total_expense_month: float
    total_savings_month: float
    net_this_month: float = 0
    savings_rate: float = 0
    net_worth: float = 0
    budget_adherence_pct: Optional[float] = None
    monthly_tiet_kiem: float = 0
    monthly_bds: float = 0
    cash_on_hand: float
    total_savings_active: float
    total_savings_target: float
    total_assets_current: float = 0
    total_assets_purchase: float = 0
    total_assets_count: int = 0
    total_projects_paid: float = 0
    active_projects_count: int
    completed_projects_count: int

class MonthlyData(BaseModel):
    month: str
    income: float
    expense: float
    savings: float

class CategorySummary(BaseModel):
    category_name: str
    category_color: str
    total: float
    percentage: float

class DashboardData(BaseModel):
    summary: DashboardSummary
    monthly_trend: List[MonthlyData]
    expense_by_category: List[CategorySummary]
    income_by_category: List[CategorySummary]
    recent_transactions: List[Transaction]
    upcoming_maturities: List[SavingsBundle]
    active_projects: List[FinancialProject]
# Note Schemas
class NoteBase(BaseModel):
    title: str
    content: Optional[str] = None
    type: Optional[str] = None

class NoteCreate(NoteBase):
    pass

class NoteUpdate(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    type: Optional[str] = None

class Note(NoteBase):
    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

# Budget Schemas
class BudgetAllocationCreate(BaseModel):
    category_id: int
    year_month: str   # "2026-05"
    amount: float = Field(gt=0)

class BudgetAllocationUpdate(BaseModel):
    amount: float = Field(gt=0)

class BudgetAllocationRecord(BaseModel):
    id: int
    category_id: int
    year_month: str
    amount: float
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

class BudgetCategoryRow(BaseModel):
    category_id: int
    category_name: str
    category_color: str
    monthly_allocation: float     # amount set for this specific month
    cumulative_allocated: float   # sum of all allocations up to this month
    cumulative_spent: float       # sum of all spending since 2026-05-01 up to end of month
    this_month_spent: float
    available_balance: float      # cumulative_allocated - cumulative_spent
    usage_pct: float              # this_month_spent / monthly_allocation * 100
    allocation_id: Optional[int]  # None if inherited
