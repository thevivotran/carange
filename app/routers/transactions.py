from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import func, extract
from typing import List, Optional
from datetime import date
import csv
import io
import random

from app.models.database import get_db, Transaction, Category, TransactionType, SavingsBundle, FinancialProject
from app.models.schemas import Transaction as TransactionSchema, TransactionCreate, TransactionUpdate

router = APIRouter()

@router.get("/", response_model=List[TransactionSchema])
def get_transactions(
    skip: int = 0,
    limit: int = 100,
    type: Optional[str] = None,
    category_id: Optional[int] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    search: Optional[str] = None,
    db: Session = Depends(get_db)
):
    # Validate date range
    if start_date and end_date and start_date > end_date:
        raise HTTPException(status_code=400, detail="Start date cannot be after end date")

    query = db.query(Transaction)

    if type:
        query = query.filter(Transaction.type == type)
    if category_id:
        query = query.filter(Transaction.category_id == category_id)
    if start_date:
        query = query.filter(Transaction.date >= start_date)
    if end_date:
        query = query.filter(Transaction.date <= end_date)
    if search:
        query = query.filter(Transaction.description.ilike(f'%{search}%'))

    return query.order_by(Transaction.date.desc()).offset(skip).limit(limit).all()

@router.get("/{transaction_id}", response_model=TransactionSchema)
def get_transaction(transaction_id: int, db: Session = Depends(get_db)):
    transaction = db.query(Transaction).filter(Transaction.id == transaction_id).first()
    if not transaction:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return transaction

@router.post("/", response_model=TransactionSchema)
def create_transaction(transaction: TransactionCreate, db: Session = Depends(get_db)):
    from app.models.database import SavingsBundle, SavingsStatus, SavingsType

    # Verify category exists
    category = db.query(Category).filter(Category.id == transaction.category_id).first()
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")
    
    # Verify transaction type matches category type
    if category.type != transaction.type:
        raise HTTPException(
            status_code=400, 
            detail=f"Transaction type '{transaction.type}' does not match category type '{category.type}'"
        )

    # Prepare transaction data
    transaction_data = transaction.model_dump(exclude={'savings_bundle'})

    # If creating savings bundle with transaction
    savings_bundle_id = None
    if transaction.is_savings_related and transaction.savings_bundle:
        # Create the savings bundle first
        bundle_data = transaction.savings_bundle
        db_bundle = SavingsBundle(
            name=bundle_data.name,
            bank_name=bundle_data.bank_name,
            type=bundle_data.type,
            initial_deposit=bundle_data.initial_deposit,
            current_amount=bundle_data.initial_deposit,  # Initialize current_amount with initial_deposit
            future_amount=bundle_data.future_amount,
            interest_rate=bundle_data.interest_rate,
            start_date=bundle_data.start_date,
            maturity_date=bundle_data.maturity_date,
            notes=bundle_data.notes,
            status=SavingsStatus.ACTIVE
        )
        db.add(db_bundle)
        db.flush()  # Get the ID without committing
        savings_bundle_id = db_bundle.id

    # Create transaction with savings_bundle_id if applicable
    transaction_data['savings_bundle_id'] = savings_bundle_id
    db_transaction = Transaction(**transaction_data)
    db.add(db_transaction)
    db.commit()
    db.refresh(db_transaction)

    return db_transaction

@router.put("/{transaction_id}", response_model=TransactionSchema)
def update_transaction(transaction_id: int, transaction: TransactionUpdate, db: Session = Depends(get_db)):
    db_transaction = db.query(Transaction).filter(Transaction.id == transaction_id).first()
    if not db_transaction:
        raise HTTPException(status_code=404, detail="Transaction not found")
    
    # Update only fields that are provided (not None)
    update_data = transaction.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(db_transaction, key, value)
    
    db.commit()
    db.refresh(db_transaction)
    return db_transaction

@router.delete("/{transaction_id}")
def delete_transaction(transaction_id: int, db: Session = Depends(get_db)):
    transaction = db.query(Transaction).filter(Transaction.id == transaction_id).first()
    if not transaction:
        raise HTTPException(status_code=404, detail="Transaction not found")
    
    db.delete(transaction)
    db.commit()
    return {"message": "Transaction deleted successfully"}

@router.get("/stats/monthly-summary")
def get_monthly_summary(
    year: Optional[int] = None,
    month: Optional[int] = None,
    db: Session = Depends(get_db)
):
    if not year:
        year = date.today().year
    if not month:
        month = date.today().month

    # Income (all income transactions)
    income = db.query(func.sum(Transaction.amount)).filter(
        extract('year', Transaction.date) == year,
        extract('month', Transaction.date) == month,
        Transaction.type == TransactionType.INCOME
    ).scalar() or 0

    # Expense (only non-savings-related expenses)
    expense = db.query(func.sum(Transaction.amount)).filter(
        extract('year', Transaction.date) == year,
        extract('month', Transaction.date) == month,
        Transaction.type == TransactionType.EXPENSE,
        Transaction.is_savings_related == False
    ).scalar() or 0

    # Savings (savings-related expenses)
    savings = db.query(func.sum(Transaction.amount)).filter(
        extract('year', Transaction.date) == year,
        extract('month', Transaction.date) == month,
        Transaction.type == TransactionType.EXPENSE,
        Transaction.is_savings_related == True
    ).scalar() or 0

    # Calculate all-time cash on hand
    total_income_all_time = db.query(func.sum(Transaction.amount)).filter(
        Transaction.type == TransactionType.INCOME
    ).scalar() or 0

    total_expense_all_time = db.query(func.sum(Transaction.amount)).filter(
        Transaction.type == TransactionType.EXPENSE
    ).scalar() or 0

    cash_on_hand = total_income_all_time - total_expense_all_time

    return {
        "year": year,
        "month": month,
        "income": income,
        "expense": expense,
        "savings": savings,
        "net": income - expense - savings,
        "cash_on_hand": cash_on_hand
    }

@router.get("/stats/by-category")
def get_transactions_by_category(
    type: str,
    year: Optional[int] = None,
    month: Optional[int] = None,
    db: Session = Depends(get_db)
):
    if not year:
        year = date.today().year
    if not month:
        month = date.today().month
    
    results = db.query(
        Category.name,
        Category.color,
        func.sum(Transaction.amount).label('total')
    ).join(Transaction).filter(
        extract('year', Transaction.date) == year,
        extract('month', Transaction.date) == month,
        Transaction.type == type
    ).group_by(Category.id).all()
    
    return [
        {"category": name, "color": color, "total": total}
        for name, color, total in results
    ]

@router.post("/bulk-upload")
def bulk_upload_transactions(
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    """
    Bulk upload transactions from CSV file.
    
    CSV Format (Vietnamese headers):
    - date: Date (YYYY-MM-DD) OR
    - year: Year (YYYY) AND month: Month (1-12) - will use 1st of month
    - amount: Amount (positive number)
    - type: 'income' or 'expense'
    - category: Category name (will auto-create if not exists)
    - description: Description (optional)
    - payment_method: cash, credit_card, debit_card, bank_transfer, mobile_payment, other (optional, default: cash)
    
    Alternative format (compatible with existing import):
    - Năm: Year
    - Tháng: Month
    - Thu: Income amount
    - Chi: Expense amount
    - Loại: Category name
    - Ghi chú: Description
    """
    # Read CSV content
    content = file.file.read()
    try:
        decoded = content.decode('utf-8-sig')
    except UnicodeDecodeError:
        try:
            decoded = content.decode('utf-8')
        except:
            return JSONResponse(
                status_code=400,
                content={"error": "Unable to decode CSV file. Please ensure it's UTF-8 encoded."}
            )
    
    reader = csv.DictReader(io.StringIO(decoded))
    fieldnames = reader.fieldnames
    
    if not fieldnames:
        return JSONResponse(
            status_code=400,
            content={"error": "CSV file is empty or has no headers"}
        )
    
    # Detect CSV format
    # Format 1: Vietnamese (Năm, Tháng, Thu, Chi, Loại, Ghi chú)
    # Format 2: English (date, amount, type, category, description, payment_method)
    is_vietnamese_format = 'Năm' in fieldnames or 'Tháng' in fieldnames
    
    if is_vietnamese_format:
        return _process_vietnamese_format(reader, fieldnames, db)
    else:
        return _process_english_format(reader, fieldnames, db)

def _process_vietnamese_format(reader, fieldnames, db):
    """Process Vietnamese CSV format"""
    # Map column names
    found_columns = {}
    required_mappings = {'Năm': 'year', 'Tháng': 'month', 'Loại': 'category'}
    
    for col in fieldnames:
        col_stripped = col.strip()
        if col_stripped in required_mappings:
            found_columns[col_stripped] = required_mappings[col_stripped]
    
    # Check for required columns
    missing = [k for k in required_mappings.keys() if k not in found_columns]
    if missing:
        return JSONResponse(
            status_code=400,
            content={"error": f"Missing required columns: {', '.join(missing)}"}
        )
    
    stats = {'income': 0, 'expense': 0, 'skipped': 0, 'errors': []}
    
    for row_num, row in enumerate(reader, start=2):
        try:
            year = int(row.get(found_columns.get('Năm', 'Năm'), 0))
            month = int(row.get(found_columns.get('Tháng', 'Tháng'), 0))
            
            # Get amounts
            thu_key = 'Thu' if 'Thu' in row else None
            chi_key = 'Chi' if 'Chi' in row else None
            
            thu_str = row.get(thu_key, '0').strip().replace(',', '').replace('.', '') if thu_key else '0'
            chi_str = row.get(chi_key, '0').strip().replace(',', '').replace('.', '') if chi_key else '0'
            
            thu = float(thu_str) if thu_str else 0
            chi = float(chi_str) if chi_str else 0
            
            category_name = row.get(found_columns.get('Loại', 'Loại'), '').strip()
            desc_key = 'Ghi chú' if 'Ghi chú' in row else None
            description = row.get(desc_key, '').strip() if desc_key else None
            
            if not year or not month:
                stats['skipped'] += 1
                continue
            
            # Create date (1st of month)
            transaction_date = date(year, month, 1)
            
            # Process income
            if thu > 0:
                category = _get_or_create_category(db, category_name, TransactionType.INCOME)
                if not _is_duplicate(db, transaction_date, thu, TransactionType.INCOME, category.id):
                    _create_transaction(db, transaction_date, thu, TransactionType.INCOME, category.id, description)
                    stats['income'] += 1
                else:
                    stats['skipped'] += 1

            # Process expense
            if chi > 0:
                category = _get_or_create_category(db, category_name, TransactionType.EXPENSE)
                if not _is_duplicate(db, transaction_date, chi, TransactionType.EXPENSE, category.id):
                    _create_transaction(db, transaction_date, chi, TransactionType.EXPENSE, category.id, description)
                    stats['expense'] += 1
                else:
                    stats['skipped'] += 1
            
            if thu == 0 and chi == 0:
                stats['skipped'] += 1
                
        except Exception as e:
            stats['errors'].append(f"Row {row_num}: {str(e)}")
            stats['skipped'] += 1
    
    db.commit()
    return {
        "success": True,
        "message": f"Successfully imported {stats['income']} income and {stats['expense']} expense transactions",
        "stats": stats
    }

def _process_english_format(reader, fieldnames, db):
    """Process English CSV format"""
    # Normalize fieldnames
    field_map = {}
    for col in fieldnames:
        col_lower = col.strip().lower()
        if col_lower in ['date', 'transaction_date']:
            field_map['date'] = col
        elif col_lower in ['amount', 'so_tien', 'amount_vnd']:
            field_map['amount'] = col
        elif col_lower in ['type', 'loai', 'transaction_type']:
            field_map['type'] = col
        elif col_lower in ['category', 'loai', 'danh_muc', 'category_name']:
            field_map['category'] = col
        elif col_lower in ['description', 'desc', 'ghi_chu', 'note', 'notes']:
            field_map['description'] = col
        elif col_lower in ['payment_method', 'payment', 'pttt', 'phuong_thuc']:
            field_map['payment_method'] = col
    
    # Check required fields
    required = ['date', 'amount', 'type', 'category']
    missing = [r for r in required if r not in field_map]
    if missing:
        return JSONResponse(
            status_code=400,
            content={
                "error": f"Missing required columns: {', '.join(missing)}. Required: date, amount, type, category"
            }
        )
    
    stats = {'income': 0, 'expense': 0, 'skipped': 0, 'errors': []}
    
    for row_num, row in enumerate(reader, start=2):
        try:
            # Parse date
            date_str = row.get(field_map['date'], '').strip()
            try:
                transaction_date = date.fromisoformat(date_str)
            except:
                stats['errors'].append(f"Row {row_num}: Invalid date format '{date_str}'")
                stats['skipped'] += 1
                continue
            
            # Parse amount
            amount_str = row.get(field_map['amount'], '0').strip().replace(',', '').replace('.', '')
            try:
                amount = abs(float(amount_str))
            except:
                stats['errors'].append(f"Row {row_num}: Invalid amount '{amount_str}'")
                stats['skipped'] += 1
                continue
            
            # Parse type
            type_str = row.get(field_map['type'], '').strip().lower()
            if type_str in ['income', 'thu', 'in']:
                trans_type = TransactionType.INCOME
            elif type_str in ['expense', 'chi', 'out']:
                trans_type = TransactionType.EXPENSE
            else:
                stats['errors'].append(f"Row {row_num}: Invalid type '{type_str}'")
                stats['skipped'] += 1
                continue
            
            # Parse category
            category_name = row.get(field_map['category'], '').strip()
            if not category_name:
                stats['errors'].append(f"Row {row_num}: Missing category")
                stats['skipped'] += 1
                continue
            
            # Get or create category
            category = _get_or_create_category(db, category_name, trans_type)

            # Parse optional fields
            description = row.get(field_map.get('description', ''), '').strip() or None
            payment_method = row.get(field_map.get('payment_method', ''), 'cash').strip().lower().replace(' ', '_')
            valid_payments = ['cash', 'credit_card', 'debit_card', 'bank_transfer', 'mobile_payment', 'other']
            if payment_method not in valid_payments:
                payment_method = 'cash'

            # Skip duplicates
            if _is_duplicate(db, transaction_date, amount, trans_type, category.id):
                stats['skipped'] += 1
                continue

            # Create transaction
            _create_transaction(db, transaction_date, amount, trans_type, category.id, description, payment_method)

            if trans_type == TransactionType.INCOME:
                stats['income'] += 1
            else:
                stats['expense'] += 1
                
        except Exception as e:
            stats['errors'].append(f"Row {row_num}: {str(e)}")
            stats['skipped'] += 1
    
    db.commit()
    return {
        "success": True,
        "message": f"Successfully imported {stats['income']} income and {stats['expense']} expense transactions",
        "stats": stats
    }

def _is_duplicate(db, trans_date: date, amount: float, trans_type: TransactionType, category_id: int) -> bool:
    """Return True if a transaction with same date/amount/type/category already exists."""
    return db.query(Transaction).filter(
        Transaction.date == trans_date,
        Transaction.amount == amount,
        Transaction.type == trans_type,
        Transaction.category_id == category_id
    ).first() is not None


def _get_or_create_category(db, category_name: str, trans_type: TransactionType):
    """Get or create a category"""
    category = db.query(Category).filter(
        Category.name == category_name,
        Category.type == trans_type
    ).first()
    
    if category:
        return category
    
    # Generate random color
    colors = ["#EF4444", "#F59E0B", "#10B981", "#3B82F6", "#6366F1", "#8B5CF6", "#EC4899"]
    
    category = Category(
        name=category_name,
        type=trans_type,
        color=random.choice(colors),
        icon="circle",
        is_active=True
    )
    db.add(category)
    db.flush()
    return category

def _create_transaction(db, trans_date: date, amount: float, trans_type: TransactionType, 
                        category_id: int, description: str = None, payment_method: str = 'cash'):
    """Create a transaction"""
    transaction = Transaction(
        date=trans_date,
        amount=amount,
        type=trans_type,
        category_id=category_id,
        description=description,
        payment_method=payment_method,
        is_savings_related=False
    )
    db.add(transaction)
    return transaction