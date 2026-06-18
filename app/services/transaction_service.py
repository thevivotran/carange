"""Transaction domain business logic."""

import csv
import logging
import io
import math
import random
import threading
from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models.database import (
    AuditField,
    Category,
    FinancialProject,
    PaymentStatus,
    ProjectPayment,
    SavingsBundle,
    SavingsStatus,
    Transaction,
    TransactionAuditLog,
    TransactionType,
)
from app.models.schemas import SavingsBundleCreate, TransactionCreate
from app.services.fiscal_period import fiscal_window_ym, get_month_start_day
from app.services.rules_service import apply_rules, normalize_description

log = logging.getLogger("app.transaction_service")

_KHAC_NAME_MAP = {"income": "Thu nhập khác", "expense": "Chi phí khác"}
_AUDIT_FIELDS = list(AuditField)


def check_duplicate(
    db: Session,
    trans_date: date,
    amount: float,
    trans_type: TransactionType,
    category_id: int,
    window_days: int = 1,
) -> list[Transaction]:
    """Return up to 5 active transactions matching the given criteria within ±window_days."""
    return (
        db.query(Transaction)
        .filter(
            Transaction.date >= trans_date - timedelta(days=window_days),
            Transaction.date <= trans_date + timedelta(days=window_days),
            Transaction.amount == amount,
            Transaction.type == trans_type,
            Transaction.category_id == category_id,
            Transaction.deleted_at.is_(None),
        )
        .limit(5)
        .all()
    )


def write_audit_log(
    db: Session,
    transaction_id: int,
    before: dict,
    transaction: Transaction,
    now: datetime,
) -> None:
    """Write audit log entries for every field that changed between before and after."""
    for field in _AUDIT_FIELDS:
        old = before.get(field)
        new = getattr(transaction, field.value)
        if old != new:
            db.add(
                TransactionAuditLog(
                    transaction_id=transaction_id,
                    changed_at=now,
                    field_name=field,
                    old_value=str(old) if old is not None else None,
                    new_value=str(new) if new is not None else None,
                )
            )


def snapshot_audit_fields(transaction: Transaction) -> dict:
    """Capture current values of all audited fields."""
    return {f: getattr(transaction, f.value) for f in _AUDIT_FIELDS}


def get_or_create_category(db: Session, category_name: str, trans_type: TransactionType) -> Category:
    """Return an existing category by name+type, or create one with a random colour."""
    if category_name.strip() == "Khác":
        category_name = _KHAC_NAME_MAP.get(trans_type.value, category_name)

    category = db.query(Category).filter(Category.name == category_name, Category.type == trans_type).first()
    if category:
        return category

    colors = ["#EF4444", "#F59E0B", "#10B981", "#3B82F6", "#6366F1", "#8B5CF6", "#EC4899"]
    category = Category(name=category_name, type=trans_type, color=random.choice(colors), icon="circle", is_active=True)
    db.add(category)
    db.flush()
    return category


def create_transaction(db: Session, data: TransactionCreate) -> Transaction:
    """Create a Transaction, optionally creating a linked SavingsBundle atomically."""
    transaction_data = data.model_dump(exclude={"savings_bundle"})
    savings_bundle_id = data.savings_bundle_id

    try:
        if data.is_savings_related and data.savings_bundle:
            bundle_data: SavingsBundleCreate = data.savings_bundle
            db_bundle = SavingsBundle(
                name=bundle_data.name,
                bank_name=bundle_data.bank_name,
                type=bundle_data.type,
                initial_deposit=bundle_data.initial_deposit,
                current_amount=bundle_data.initial_deposit,
                future_amount=bundle_data.future_amount,
                interest_rate=bundle_data.interest_rate,
                start_date=bundle_data.start_date,
                maturity_date=bundle_data.maturity_date,
                notes=bundle_data.notes,
                status=SavingsStatus.ACTIVE,
            )
            db.add(db_bundle)
            db.flush()
            savings_bundle_id = db_bundle.id

        transaction_data["savings_bundle_id"] = savings_bundle_id

        # Resolve payee_id without touching the description
        _, payee_id = normalize_description(db, transaction_data.get("description") or "")
        transaction_data["payee_id"] = payee_id

        db_tx = Transaction(**transaction_data)
        db.add(db_tx)
        db.flush()

        # Apply rules (may override category, set auto_approve)
        action = apply_rules(db, db_tx, payee_id)
        if action.category_id is not None:
            db_tx.category_id = action.category_id
        if action.force_needs_review:
            db_tx.needs_review = True

        db.commit()
        db.refresh(db_tx)

        from app.services.settings_service import get_telegram_config

        _tg_cfg = get_telegram_config(db)

        _ping_fields = {
            "tx_id": db_tx.id,
            "amount": db_tx.amount,
            "tx_type": db_tx.type.value if db_tx.type else "expense",
            "cat_name": db_tx.category.name if db_tx.category else "?",
            "description": db_tx.description or "",
            "source": db_tx.source or "manual",
            "needs_review": bool(db_tx.needs_review),
            "bot_token": _tg_cfg["telegram_bot_token"],
            "chat_id": _tg_cfg["telegram_chat_id"],
            "app_url": _tg_cfg["app_url"],
        }

        try:
            if db_tx.type == TransactionType.EXPENSE and db_tx.category_id:
                from app.services.budget_context import budget_snapshot
                from app.services.fiscal_period import current_period_label, get_month_start_day

                _day = get_month_start_day(db)
                _label = current_period_label(db_tx.date, _day)
                _snap = budget_snapshot(db, db_tx.category_id, _label, day=_day)
                if _snap:
                    _ping_fields["budget_snapshot"] = _snap
        except Exception as exc:
            log.warning("Budget snapshot failed for tx %d: %s", db_tx.id, exc)

        def _ping(fields: dict) -> None:
            try:
                from app.notify import telegram as _tg

                _tg.send_transaction_ping_fields(fields)
            except Exception as exc:
                log.warning("Telegram ping failed for tx %d: %s", fields["tx_id"], exc)

        threading.Thread(target=_ping, args=(_ping_fields,), daemon=True).start()

        return db_tx
    except Exception:
        db.rollback()
        raise


def cascade_delete_payment(db: Session, transaction_id: int) -> None:
    """Revert a linked ProjectPayment to PENDING and recompute project totals."""
    payment = db.query(ProjectPayment).filter(ProjectPayment.transaction_id == transaction_id).first()
    if not payment:
        return
    payment.status = PaymentStatus.PENDING
    payment.transaction_id = None
    project = db.query(FinancialProject).filter(FinancialProject.id == payment.project_id).first()
    if project:
        all_payments = db.query(ProjectPayment).filter(ProjectPayment.project_id == project.id).all()
        project.target_amount = sum(p.amount for p in all_payments)
        project.current_amount = sum(p.amount for p in all_payments if p.status == PaymentStatus.PAID)


def soft_delete_transaction(db: Session, transaction_id: int) -> Transaction:
    """Soft-delete a transaction and cascade to linked project payment."""
    tx = db.query(Transaction).filter(Transaction.id == transaction_id, Transaction.deleted_at.is_(None)).first()
    if tx is None:
        raise LookupError("Transaction not found")
    try:
        cascade_delete_payment(db, transaction_id)
        tx.deleted_at = datetime.now(timezone.utc)
        db.commit()
        return tx
    except Exception:
        db.rollback()
        raise


def restore_transaction(db: Session, transaction_id: int) -> Transaction:
    """Restore a soft-deleted transaction."""
    tx = db.query(Transaction).filter(Transaction.id == transaction_id, Transaction.deleted_at.isnot(None)).first()
    if tx is None:
        raise LookupError("Transaction not found in trash")
    tx.deleted_at = None
    db.commit()
    db.refresh(tx)
    return tx


def _build_transaction_from_row(
    db: Session,
    trans_date: date,
    amount: float,
    trans_type: TransactionType,
    category_id: int,
    description: str | None = None,
    payment_method: str = "cash",
) -> Transaction:
    """Instantiate, add, and flush a plain Transaction (no commit).

    Flushing per row surfaces DB constraint violations immediately so a single
    bad row doesn't roll back the entire batch at commit time.
    """
    tx = Transaction(
        date=trans_date,
        amount=amount,
        type=trans_type,
        category_id=category_id,
        description=description,
        payment_method=payment_method,
        is_savings_related=False,
    )
    db.add(tx)
    db.flush()
    return tx


def _is_csv_duplicate(
    db: Session,
    trans_date: date,
    amount: float,
    trans_type: TransactionType,
    category_id: int,
    description: str | None = None,
) -> bool:
    """Exact-match duplicate check used during CSV import (no date window)."""
    q = db.query(Transaction).filter(
        Transaction.date == trans_date,
        Transaction.amount == amount,
        Transaction.type == trans_type,
        Transaction.category_id == category_id,
        Transaction.deleted_at.is_(None),
    )
    if description:
        q = q.filter(Transaction.description == description)
    else:
        q = q.filter(Transaction.description.is_(None))
    return q.first() is not None


def parse_csv_vietnamese(content: bytes, db: Session) -> dict:
    """Parse and import a Vietnamese-format CSV. Returns stats dict."""
    try:
        decoded = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        decoded = content.decode("utf-8")

    reader = csv.DictReader(io.StringIO(decoded))
    fieldnames = reader.fieldnames or []

    required_mappings = {"Năm": "year", "Tháng": "month", "Loại": "category"}
    found_columns: dict[str, str] = {}
    for col in fieldnames:
        col_stripped = col.strip()
        if col_stripped in required_mappings:
            found_columns[required_mappings[col_stripped]] = col

    missing = [v for v in required_mappings.values() if v not in found_columns]
    if missing:
        raise ValueError(f"Missing required columns: {', '.join(missing)}")

    stats: dict = {"income": 0, "expense": 0, "skipped": 0, "errors": []}
    month_start_day = get_month_start_day(db)

    for row_num, row in enumerate(reader, start=2):
        try:
            year_str = row.get(found_columns["year"], "").strip()
            month_str = row.get(found_columns["month"], "").strip()
            year = int(year_str) if year_str else 0
            month = int(month_str) if month_str else 0

            thu_key = next((col for col in row if col.strip() == "Thu"), None)
            chi_key = next((col for col in row if col.strip() == "Chi"), None)

            thu_str = row.get(thu_key, "0").strip().replace(",", "").replace(".", "") if thu_key else "0"
            chi_str = row.get(chi_key, "0").strip().replace(",", "").replace(".", "") if chi_key else "0"

            thu = float(thu_str) if thu_str else 0
            chi = float(chi_str) if chi_str else 0

            category_name = row.get(found_columns["category"], "").strip()
            desc_key = next((col for col in row if col.strip() == "Ghi chú"), None)
            description = row.get(desc_key, "").strip() if desc_key else None

            if not year or not month:
                stats["skipped"] += 1
                continue
            if not category_name:
                stats["errors"].append(f"Row {row_num}: Missing category name")
                stats["skipped"] += 1
                continue

            transaction_date, _ = fiscal_window_ym(year, month, month_start_day)

            if thu > 0:
                cat = get_or_create_category(db, category_name, TransactionType.INCOME)
                if not _is_csv_duplicate(db, transaction_date, thu, TransactionType.INCOME, cat.id):
                    _build_transaction_from_row(db, transaction_date, thu, TransactionType.INCOME, cat.id, description)
                    stats["income"] += 1
                else:
                    stats["skipped"] += 1

            if chi > 0:
                cat = get_or_create_category(db, category_name, TransactionType.EXPENSE)
                if not _is_csv_duplicate(db, transaction_date, chi, TransactionType.EXPENSE, cat.id):
                    _build_transaction_from_row(db, transaction_date, chi, TransactionType.EXPENSE, cat.id, description)
                    stats["expense"] += 1
                else:
                    stats["skipped"] += 1

            if thu == 0 and chi == 0:
                stats["skipped"] += 1

        except Exception as e:
            stats["errors"].append(f"Row {row_num}: {e!s}")
            stats["skipped"] += 1

    db.commit()
    return stats


def parse_csv_english(content: bytes, db: Session) -> dict:
    """Parse and import an English-format CSV. Returns stats dict."""
    try:
        decoded = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        decoded = content.decode("utf-8")

    reader = csv.DictReader(io.StringIO(decoded))
    fieldnames = reader.fieldnames or []

    field_map: dict[str, str] = {}
    for col in fieldnames:
        col_lower = col.strip().lower()
        if col_lower in ["date", "transaction_date"]:
            field_map["date"] = col
        elif col_lower in ["amount", "so_tien", "amount_vnd"]:
            field_map["amount"] = col
        elif col_lower in ["type", "transaction_type"]:
            field_map["type"] = col
        elif col_lower in ["category", "loai", "danh_muc", "category_name"]:
            field_map["category"] = col
        elif col_lower in ["description", "desc", "ghi_chu", "note", "notes"]:
            field_map["description"] = col
        elif col_lower in ["payment_method", "payment", "pttt", "phuong_thuc"]:
            field_map["payment_method"] = col

    required = ["date", "amount", "type", "category"]
    missing = [r for r in required if r not in field_map]
    if missing:
        raise ValueError(f"Missing required columns: {', '.join(missing)}. Required: date, amount, type, category")

    stats: dict = {"income": 0, "expense": 0, "skipped": 0, "errors": []}
    valid_payments = ["cash", "credit_card", "debit_card", "bank_transfer", "mobile_payment", "other"]

    for row_num, row in enumerate(reader, start=2):
        try:
            date_str = row.get(field_map["date"], "").strip()
            try:
                transaction_date = date.fromisoformat(date_str)
            except Exception:
                stats["errors"].append(f"Row {row_num}: Invalid date format '{date_str}'")
                stats["skipped"] += 1
                continue

            amount_str = row.get(field_map["amount"], "0").strip().replace(",", "")
            try:
                amount = abs(float(amount_str))
                if not math.isfinite(amount):
                    raise ValueError("non-finite")
            except Exception:
                stats["errors"].append(f"Row {row_num}: Invalid amount '{amount_str}'")
                stats["skipped"] += 1
                continue
            if amount <= 0:
                stats["errors"].append(f"Row {row_num}: Amount must be greater than 0")
                stats["skipped"] += 1
                continue

            type_str = row.get(field_map["type"], "").strip().lower()
            if type_str in ["income", "thu", "in"]:
                trans_type = TransactionType.INCOME
            elif type_str in ["expense", "chi", "out"]:
                trans_type = TransactionType.EXPENSE
            else:
                stats["errors"].append(f"Row {row_num}: Invalid type '{type_str}'")
                stats["skipped"] += 1
                continue

            category_name = row.get(field_map["category"], "").strip()
            if not category_name:
                stats["errors"].append(f"Row {row_num}: Missing category")
                stats["skipped"] += 1
                continue

            category = get_or_create_category(db, category_name, trans_type)
            description = row.get(field_map.get("description", ""), "").strip() or None
            payment_method = row.get(field_map.get("payment_method", ""), "cash").strip().lower().replace(" ", "_")
            if payment_method not in valid_payments:
                payment_method = "cash"

            if _is_csv_duplicate(db, transaction_date, amount, trans_type, category.id, description):
                stats["skipped"] += 1
                continue

            _build_transaction_from_row(
                db, transaction_date, amount, trans_type, category.id, description, payment_method
            )

            if trans_type == TransactionType.INCOME:
                stats["income"] += 1
            else:
                stats["expense"] += 1

        except Exception as e:
            stats["errors"].append(f"Row {row_num}: {e!s}")
            stats["skipped"] += 1

    db.commit()
    return stats
