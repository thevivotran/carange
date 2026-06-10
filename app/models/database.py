from sqlalchemy import (
    JSON,
    create_engine,
    BigInteger,
    Column,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Float,
    DateTime,
    Date,
    Boolean,
    ForeignKey,
    Text,
    Index,
    UniqueConstraint,
)
from sqlalchemy.orm import declarative_base, deferred, sessionmaker, relationship
from sqlalchemy import types as sa_types
from datetime import datetime, timezone
import enum


class CIEnum(sa_types.TypeDecorator):
    """Case-insensitive enum column: always writes lowercase, tolerates any case on read."""

    impl = sa_types.String
    cache_ok = True

    def __init__(self, enum_class, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._enum_class = enum_class

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, self._enum_class):
            return value.value.lower()
        return str(value).lower()

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return self._enum_class(value.lower())


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
    PAID = "paid"


class AssetType(str, enum.Enum):
    CURRENCY = "currency"
    GOLD = "gold"
    OTHER = "other"


class ImportJobStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"


class ImportSource(str, enum.Enum):
    TIMO = "timo"
    SHOPEE = "shopee"
    GRAB = "grab"
    UOB = "uob"
    LIOBANK = "liobank"


class AuditField(str, enum.Enum):
    DATE = "date"
    AMOUNT = "amount"
    TYPE = "type"
    CATEGORY_ID = "category_id"
    DESCRIPTION = "description"
    PAYMENT_METHOD = "payment_method"
    IS_SAVINGS_RELATED = "is_savings_related"
    IS_ADVANCE = "is_advance"
    ADVANCE_SETTLED = "advance_settled"
    NEEDS_REVIEW = "needs_review"


class ImportJob(Base):
    __tablename__ = "import_jobs"

    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String(255), nullable=False)
    file_path = Column(String(500), nullable=False)
    image_hash = Column(String(64), nullable=False, unique=True)  # SHA-256, dedup guard
    source_hint = Column(CIEnum(ImportSource), nullable=True)
    detected_source = Column(CIEnum(ImportSource), nullable=True)
    status = Column(CIEnum(ImportJobStatus), default=ImportJobStatus.PENDING, nullable=False)
    error_message = Column(Text, nullable=True)
    transaction_count = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    started_at = Column(DateTime(timezone=True), nullable=True)
    processed_at = Column(DateTime(timezone=True), nullable=True)
    retry_count = Column(Integer, default=0, nullable=False, server_default="0")
    retry_after = Column(DateTime(timezone=True), nullable=True)

    transactions = relationship("Transaction", back_populates="import_job")


class Category(Base):
    __tablename__ = "categories"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    type = Column(CIEnum(TransactionType), nullable=False)
    color = Column(String(7), default="#3B82F6")
    icon = Column(String(50), default="circle")
    is_active = Column(Boolean, default=True)
    is_wealth_building = Column(Boolean, default=False, nullable=False, server_default="0")
    is_passive_income = Column(Boolean, default=False, nullable=False, server_default="0")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    transactions = relationship("Transaction", back_populates="category")
    budget_allocations = relationship("BudgetAllocation", cascade="all, delete-orphan")


class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, index=True)
    date = Column(Date, nullable=False, index=True)
    amount = Column(Numeric(18, 0), nullable=False)
    type = Column(CIEnum(TransactionType), nullable=False)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=False)
    description = Column(Text, nullable=True)
    payment_method = Column(String(50), default="cash")
    is_savings_related = Column(Boolean, default=False)
    is_advance = Column(Boolean, default=False)
    advance_settled = Column(Boolean, default=False)
    source = Column(String(30), default="manual", nullable=True)
    savings_bundle_id = Column(Integer, ForeignKey("savings_bundles.id"), nullable=True)
    project_id = Column(Integer, ForeignKey("financial_projects.id"), nullable=True)
    import_job_id = Column(Integer, ForeignKey("import_jobs.id"), nullable=True)
    email_ingest_log_id = Column(Integer, ForeignKey("email_ingest_log.id"), nullable=True)
    payee_id = Column(Integer, ForeignKey("payees.id"), nullable=True)
    confidence_score = Column(Float, nullable=True)
    needs_review = Column(Boolean, default=False)
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    category = relationship("Category", back_populates="transactions")
    savings_bundle = relationship("SavingsBundle", back_populates="transactions")
    project = relationship("FinancialProject", back_populates="transactions")
    import_job = relationship("ImportJob", back_populates="transactions")
    email_ingest_log = relationship("EmailIngestLog", back_populates="transactions")
    payee = relationship("Payee")
    audit_logs = relationship("TransactionAuditLog", back_populates="transaction", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_transactions_type_date", "type", "date"),
        Index("ix_transactions_category_id", "category_id"),
        Index("ix_transactions_deleted_at", "deleted_at"),
        Index("ix_transactions_import_job_id", "import_job_id"),
        Index("ix_transactions_needs_review", "needs_review"),
        Index("ix_transactions_is_savings_related", "is_savings_related"),
    )


class TransactionAuditLog(Base):
    __tablename__ = "transaction_audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    transaction_id = Column(Integer, ForeignKey("transactions.id"), nullable=False)
    changed_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    field_name = Column(CIEnum(AuditField), nullable=False)
    old_value = Column(Text, nullable=True)
    new_value = Column(Text, nullable=True)

    transaction = relationship("Transaction", back_populates="audit_logs")


class SavingsBundle(Base):
    __tablename__ = "savings_bundles"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)
    bank_name = Column(String(100), nullable=False)
    type = Column(CIEnum(SavingsType), nullable=False)
    initial_deposit = Column(Numeric(18, 0), nullable=False)
    current_amount = Column(Numeric(18, 0), nullable=False)
    future_amount = Column(Numeric(18, 0), nullable=False)
    interest_rate = Column(Float, nullable=True)  # percentage, keep Float
    start_date = Column(Date, nullable=False)
    maturity_date = Column(Date, nullable=True)
    status = Column(CIEnum(SavingsStatus), default=SavingsStatus.ACTIVE)
    notes = Column(Text, nullable=True)
    linked_project_id = Column(Integer, ForeignKey("financial_projects.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    completed_at = Column(DateTime(timezone=True), nullable=True)
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    transactions = relationship("Transaction", back_populates="savings_bundle")
    linked_project = relationship("FinancialProject", back_populates="linked_savings")

    __table_args__ = (Index("ix_savings_bundles_status", "status"),)


class FinancialProject(Base):
    __tablename__ = "financial_projects"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)
    type = Column(CIEnum(ProjectType), nullable=False)
    description = Column(Text, nullable=True)
    target_amount = Column(Numeric(18, 0), nullable=False, default=0, server_default="0")
    current_amount = Column(Numeric(18, 0), default=0, server_default="0")
    priority = Column(CIEnum(Priority), default=Priority.MEDIUM)
    status = Column(CIEnum(ProjectStatus), default=ProjectStatus.PLANNING)
    deadline = Column(Date, nullable=True)
    default_category_id = Column(Integer, ForeignKey("categories.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    completed_at = Column(DateTime(timezone=True), nullable=True)
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    transactions = relationship("Transaction", back_populates="project")
    linked_savings = relationship("SavingsBundle", back_populates="linked_project")
    payments = relationship("ProjectPayment", back_populates="project", cascade="all, delete-orphan")

    __table_args__ = (Index("ix_financial_projects_status", "status"),)


class ProjectPayment(Base):
    __tablename__ = "project_payments"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("financial_projects.id"), nullable=False)
    due_date = Column(Date, nullable=True)
    amount = Column(Numeric(18, 0), nullable=False)
    status = Column(CIEnum(PaymentStatus), default=PaymentStatus.PENDING, nullable=False)
    notes = Column(Text, nullable=True)
    sort_order = Column(Integer, default=0)
    transaction_id = Column(Integer, ForeignKey("transactions.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    project = relationship("FinancialProject", back_populates="payments")
    transaction = relationship("Transaction")


class OtherAsset(Base):
    __tablename__ = "other_assets"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)
    asset_type = Column(CIEnum(AssetType), nullable=False)
    symbol = Column(String(20), nullable=True)
    quantity = Column(Float, nullable=False)  # physical quantity (grams, units), keep Float
    unit = Column(String(50), nullable=False)
    purchase_price_vnd = Column(Numeric(18, 0), nullable=False)
    current_value_vnd = Column(Numeric(18, 0), nullable=False)
    notes = Column(Text, nullable=True)
    acquired_date = Column(Date, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class TransactionTemplate(Base):
    __tablename__ = "transaction_templates"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)
    amount = Column(Numeric(18, 0), nullable=False)
    type = Column(CIEnum(TransactionType), nullable=False)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=False)
    description = Column(Text, nullable=True)
    payment_method = Column(String(50), default="cash")
    is_active = Column(Boolean, default=True)
    cadence = Column(String(20), nullable=True)  # daily|weekly|monthly|yearly
    next_run_at = Column(Date, nullable=True)
    last_run_at = Column(Date, nullable=True)
    auto_approve = Column(Boolean, default=False, nullable=False, server_default="0")
    lead_days = Column(Integer, default=0, nullable=False, server_default="0")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    category = relationship("Category")


class BudgetAllocation(Base):
    __tablename__ = "budget_allocations"

    id = Column(Integer, primary_key=True, index=True)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=False)
    year_month = Column(String(7), nullable=False)  # "2026-05"
    amount = Column(Numeric(18, 0), nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    category = relationship("Category", back_populates="budget_allocations")

    __table_args__ = (UniqueConstraint("category_id", "year_month", name="uq_budget_category_month"),)


class TransactionRule(Base):
    __tablename__ = "transaction_rules"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    priority = Column(Integer, default=0, nullable=False)  # lower number = higher priority

    # Matcher
    match_field = Column(String(50), nullable=False)  # description|amount|payment_method|source|payee_id|type
    match_op = Column(String(20), nullable=False)  # equals|contains|regex|range|in
    match_value = Column(Text, nullable=False)

    # Action stored as JSON (JSONB in PostgreSQL via migration 0014)
    action_json = Column(JSON, nullable=False, default=dict)

    # Stats
    match_count = Column(Integer, default=0)
    last_matched_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class Payee(Base):
    __tablename__ = "payees"

    id = Column(Integer, primary_key=True, index=True)
    canonical_name = Column(String(200), nullable=False, unique=True)
    default_category_id = Column(Integer, ForeignKey("categories.id"), nullable=True)
    alias_patterns = Column(JSON, nullable=True)  # list[str] — JSONB in PostgreSQL via migration 0014
    source = Column(String(20), default="manual")  # manual|learned|bootstrap

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    default_category = relationship("Category")


class EmailIngestLog(Base):
    __tablename__ = "email_ingest_log"

    id = Column(Integer, primary_key=True, index=True)
    message_id = Column(String(500), nullable=False, unique=True)  # RFC 2822 Message-ID header
    sender = Column(String(200), nullable=True)
    subject = Column(String(500), nullable=True)
    received_at = Column(DateTime(timezone=True), nullable=True)
    processed_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(String(20), default="pending", nullable=False)  # pending|done|failed
    error_message = Column(Text, nullable=True)
    transaction_count = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    retry_count = Column(Integer, default=0, nullable=False, server_default="0")
    retry_after = Column(DateTime(timezone=True), nullable=True)
    # zlib-compressed RFC 2822 source; cleared once transactions are committed,
    # kept on failed / zero-transaction rows so they can be reprocessed.
    raw_email = deferred(Column(LargeBinary, nullable=True))
    raw_size = Column(Integer, nullable=True)  # compressed size; lets the UI test availability without loading the blob
    parser_name = Column(String(100), nullable=True)

    transactions = relationship("Transaction", back_populates="email_ingest_log")


class ImapFolderState(Base):
    """UID-based ingestion cursor per (account, folder).

    The worker processes messages strictly by ascending UID and persists the
    high-water mark here, so mailbox flags (\\Seen) never affect what gets
    ingested. last_uid resets when the server reports a new UIDVALIDITY.
    """

    __tablename__ = "imap_folder_state"

    id = Column(Integer, primary_key=True, index=True)
    account = Column(String(200), nullable=False)
    folder = Column(String(200), nullable=False)
    uidvalidity = Column(BigInteger, default=0, nullable=False)
    last_uid = Column(BigInteger, default=0, nullable=False, server_default="0")
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (UniqueConstraint("account", "folder", name="uq_imap_folder_state_account_folder"),)


class LearnedPattern(Base):
    """LLM-generated regex patterns for parsing a sender domain's emails.

    failure_count tracks consecutive misses; the row is dropped once it crosses
    the threshold so the LLM fallback re-learns the (changed) format.
    """

    __tablename__ = "learned_patterns"

    id = Column(Integer, primary_key=True, index=True)
    domain = Column(String(200), nullable=False, unique=True, index=True)
    patterns = Column(Text, nullable=False)  # JSON: amount_patterns / date_pattern / desc_pattern / type_detect
    success_count = Column(Integer, default=0, nullable=False, server_default="0")
    failure_count = Column(Integer, default=0, nullable=False, server_default="0")
    generated_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class Note(Base):
    __tablename__ = "notes"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(200), nullable=False)
    content = Column(Text, nullable=True)
    type = Column(String(50), nullable=True)  # e.g. 'money_owed', 'general'
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class Setting(Base):
    __tablename__ = "settings"

    key = Column(String(100), primary_key=True)
    value = Column(Text, nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class User(Base):
    """A household profile (Netflix-style picker — no password; Tailscale is the
    security boundary). Owns per-user UI preferences via UserSetting."""

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(50), unique=True, nullable=False)
    color = Column(String(20), nullable=False, default="#2563EB")  # avatar chip color
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    last_seen_at = Column(DateTime(timezone=True), nullable=True)


class UserSetting(Base):
    """Per-profile K-V preferences (e.g. nav_items, dashboard_sections).
    Mirrors Setting, which remains the household-wide default store."""

    __tablename__ = "user_settings"

    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    key = Column(String(100), primary_key=True)
    value = Column(Text, nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class InsightType(str, enum.Enum):
    WEEKLY_DIGEST = "weekly_digest"
    BUDGET_ADVISOR = "budget_advisor"


class AIInsight(Base):
    __tablename__ = "ai_insights"

    id = Column(Integer, primary_key=True, index=True)
    insight_type = Column(CIEnum(InsightType), unique=True, nullable=False)
    content = Column(Text, nullable=False)
    generated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    trigger_transaction_id = Column(Integer, ForeignKey("transactions.id"), nullable=True)


class PeriodRollup(Base):
    """Pre-computed period metrics. Used as a cross-pod cache invalidation signal
    (horizon='__inv__', period_key='global') and for future materialized rollups."""

    __tablename__ = "period_rollups"

    id = Column(Integer, primary_key=True, index=True)
    horizon = Column(String(10), nullable=False)  # 'monthly' | 'weekly' | '__inv__'
    period_key = Column(String(20), nullable=False)  # '2026-06' | 'global'
    payload_json = Column(JSON, nullable=False, default=dict)  # JSONB in PG via migration 0014
    computed_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (UniqueConstraint("horizon", "period_key", name="uq_period_rollups_horizon_key"),)


# Database configuration
import os
from sqlalchemy import event

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./carange.db")

_is_sqlite = DATABASE_URL.startswith("sqlite")
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if _is_sqlite else {},
    pool_pre_ping=not _is_sqlite,  # reconnect silently after postgres restarts
    **({} if _is_sqlite else {"pool_size": 3, "max_overflow": 7, "pool_timeout": 30, "pool_recycle": 1800}),
)


@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_conn, _):
    if not _is_sqlite:
        return
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA cache_size=-16000")  # 16 MB page cache
    cursor.execute("PRAGMA temp_store=MEMORY")  # temp tables in RAM
    cursor.execute("PRAGMA mmap_size=134217728")  # 128 MB memory-mapped I/O
    cursor.execute("PRAGMA foreign_keys=ON")  # enforce FK constraints
    cursor.execute("PRAGMA busy_timeout=10000")  # 10 s before "locked" error
    cursor.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def create_tables():
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
