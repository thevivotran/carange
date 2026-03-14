import os
import json
from typing import List, Dict, Any, Optional, Tuple
from datetime import date, datetime
from decimal import Decimal
import gspread
from google.oauth2.service_account import Credentials
from google.auth.exceptions import DefaultCredentialsError
from sqlalchemy.orm import Session

from app.models.database import Transaction, SavingsBundle, Category, TransactionType


class GoogleSheetsService:
    """Service for syncing data with Google Sheets."""
    
    # Sheet names
    TRANSACTIONS_SHEET = "Transactions"
    SAVINGS_SHEET = "Savings Bundles"
    
    # Column headers
    TRANSACTIONS_HEADERS = [
        "ID", "Date", "Type", "Amount", "Category", "Description", 
        "Payment Method", "Is Savings Related", "Savings Bundle ID", 
        "Project ID", "Created At", "Updated At"
    ]
    
    SAVINGS_HEADERS = [
        "ID", "Name", "Bank Name", "Type", "Initial Deposit", "Current Amount",
        "Future Amount", "Interest Rate (%)", "Start Date", "Maturity Date", "Status",
        "Notes", "Linked Project ID", "Created At", "Completed At"
    ]
    
    def __init__(self):
        self.client = None
        self.spreadsheet = None
        self.spreadsheet_id = os.getenv("GOOGLE_SPREADSHEET_ID")
        self.credentials_file = os.getenv("GOOGLE_CREDENTIALS_FILE", "google_credentials.json")
    
    def _get_client(self) -> gspread.Client:
        """Initialize and return gspread client."""
        if self.client is None:
            try:
                # Define scopes
                scopes = [
                    "https://www.googleapis.com/auth/spreadsheets",
                    "https://www.googleapis.com/auth/drive"
                ]
                
                # Load credentials from service account file
                if not os.path.exists(self.credentials_file):
                    raise FileNotFoundError(
                        f"Google credentials file not found: {self.credentials_file}. "
                        "Please download your service account credentials and place it in the project root."
                    )
                
                credentials = Credentials.from_service_account_file(
                    self.credentials_file,
                    scopes=scopes
                )
                
                self.client = gspread.authorize(credentials)
                
            except Exception as e:
                raise ConnectionError(f"Failed to initialize Google Sheets client: {str(e)}")
        
        return self.client
    
    def _get_spreadsheet(self):
        """Get or initialize spreadsheet."""
        if self.spreadsheet is None:
            if not self.spreadsheet_id:
                raise ValueError(
                    "GOOGLE_SPREADSHEET_ID environment variable not set. "
                    "Please set it to your Google Spreadsheet ID."
                )
            
            client = self._get_client()
            try:
                self.spreadsheet = client.open_by_key(self.spreadsheet_id)
            except gspread.exceptions.SpreadsheetNotFound:
                raise ValueError(
                    f"Spreadsheet with ID '{self.spreadsheet_id}' not found. "
                    "Please check the ID and ensure the service account has access."
                )
            except Exception as e:
                raise ConnectionError(f"Failed to open spreadsheet: {str(e)}")
        
        return self.spreadsheet
    
    def _format_value(self, value: Any) -> str:
        """Format a value for Google Sheets."""
        if value is None:
            return ""
        if isinstance(value, (date, datetime)):
            return value.strftime("%Y-%m-%d %H:%M:%S")
        if isinstance(value, Decimal):
            return str(float(value))
        if isinstance(value, bool):
            return "Yes" if value else "No"
        return str(value)
    
    def _parse_date(self, value: str) -> Optional[date]:
        """Parse date string from Google Sheets."""
        if not value:
            return None
        try:
            # Try different date formats
            for fmt in ["%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y", "%m/%d/%Y"]:
                try:
                    return datetime.strptime(value.split()[0], fmt).date()
                except ValueError:
                    continue
        except:
            pass
        return None
    
    def _parse_datetime(self, value: str) -> Optional[datetime]:
        """Parse datetime string from Google Sheets."""
        if not value:
            return None
        try:
            return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
        except:
            try:
                return datetime.strptime(value, "%Y-%m-%d")
            except:
                return None
    
    def _ensure_sheets_exist(self):
        """Ensure required sheets exist in the spreadsheet."""
        spreadsheet = self._get_spreadsheet()
        existing_sheets = [sheet.title for sheet in spreadsheet.worksheets()]
        
        # Create Transactions sheet if it doesn't exist
        if self.TRANSACTIONS_SHEET not in existing_sheets:
            worksheet = spreadsheet.add_worksheet(
                title=self.TRANSACTIONS_SHEET,
                rows=1000,
                cols=len(self.TRANSACTIONS_HEADERS)
            )
            worksheet.append_row(self.TRANSACTIONS_HEADERS)
        
        # Create Savings sheet if it doesn't exist
        if self.SAVINGS_SHEET not in existing_sheets:
            worksheet = spreadsheet.add_worksheet(
                title=self.SAVINGS_SHEET,
                rows=1000,
                cols=len(self.SAVINGS_HEADERS)
            )
            worksheet.append_row(self.SAVINGS_HEADERS)
    
    def test_connection(self) -> Dict[str, Any]:
        """Test Google Sheets connection and return status."""
        try:
            spreadsheet = self._get_spreadsheet()
            sheets = [sheet.title for sheet in spreadsheet.worksheets()]
            
            return {
                "success": True,
                "spreadsheet_title": spreadsheet.title,
                "spreadsheet_url": f"https://docs.google.com/spreadsheets/d/{self.spreadsheet_id}",
                "available_sheets": sheets,
                "message": "Connection successful"
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "message": "Connection failed"
            }
    
    def clear_sheet(self, sheet_name: str) -> bool:
        """Clear all data from a sheet (except headers)."""
        try:
            spreadsheet = self._get_spreadsheet()
            worksheet = spreadsheet.worksheet(sheet_name)
            
            # Get all values
            all_values = worksheet.get_all_values()
            
            if len(all_values) > 1:
                # Clear everything except header row
                worksheet.delete_rows(2, len(all_values))
            
            return True
        except gspread.exceptions.WorksheetNotFound:
            return False
        except Exception as e:
            raise Exception(f"Failed to clear sheet {sheet_name}: {str(e)}")
    
    def export_transactions(self, db: Session, clear_first: bool = False) -> int:
        """Export all transactions to Google Sheets."""
        self._ensure_sheets_exist()
        
        if clear_first:
            self.clear_sheet(self.TRANSACTIONS_SHEET)
        
        # Get all transactions with category info
        transactions = db.query(Transaction).all()
        
        spreadsheet = self._get_spreadsheet()
        worksheet = spreadsheet.worksheet(self.TRANSACTIONS_SHEET)
        
        # Prepare rows
        rows = []
        for t in transactions:
            row = [
                self._format_value(t.id),
                self._format_value(t.date),
                self._format_value(t.type.value if t.type else ""),
                self._format_value(t.amount),
                self._format_value(t.category.name if t.category else ""),
                self._format_value(t.description),
                self._format_value(t.payment_method),
                self._format_value(t.is_savings_related),
                self._format_value(t.savings_bundle_id),
                self._format_value(t.project_id),
                self._format_value(t.created_at),
                self._format_value(t.updated_at)
            ]
            rows.append(row)
        
        # Append all rows
        if rows:
            worksheet.append_rows(rows)
        
        return len(rows)
    
    def export_savings(self, db: Session, clear_first: bool = False) -> int:
        """Export all savings bundles to Google Sheets."""
        self._ensure_sheets_exist()
        
        if clear_first:
            self.clear_sheet(self.SAVINGS_SHEET)
        
        # Get all savings bundles
        bundles = db.query(SavingsBundle).all()
        
        spreadsheet = self._get_spreadsheet()
        worksheet = spreadsheet.worksheet(self.SAVINGS_SHEET)
        
        # Prepare rows
        rows = []
        for b in bundles:
            row = [
                self._format_value(b.id),
                self._format_value(b.name),
                self._format_value(b.bank_name),
                self._format_value(b.type.value if b.type else ""),
                self._format_value(b.initial_deposit),
                self._format_value(b.current_amount),
                self._format_value(b.future_amount),
                self._format_value(b.interest_rate),
                self._format_value(b.start_date),
                self._format_value(b.maturity_date),
                self._format_value(b.status.value if b.status else ""),
                self._format_value(b.notes),
                self._format_value(b.linked_project_id),
                self._format_value(b.created_at),
                self._format_value(b.completed_at)
            ]
            rows.append(row)
        
        # Append all rows
        if rows:
            worksheet.append_rows(rows)
        
        return len(rows)
    
    def import_transactions(self, db: Session, conflict_strategy: str = "skip") -> Tuple[int, int, List[str]]:
        """
        Import transactions from Google Sheets.
        
        Args:
            db: Database session
            conflict_strategy: "skip", "update", or "replace"
        
        Returns:
            Tuple of (imported_count, skipped_count, errors)
        """
        spreadsheet = self._get_spreadsheet()
        
        try:
            worksheet = spreadsheet.worksheet(self.TRANSACTIONS_SHEET)
        except gspread.exceptions.WorksheetNotFound:
            return 0, 0, [f"Sheet '{self.TRANSACTIONS_SHEET}' not found"]
        
        # Get all values (skip header)
        all_values = worksheet.get_all_values()
        if len(all_values) <= 1:
            return 0, 0, []
        
        data_rows = all_values[1:]
        imported = 0
        skipped = 0
        errors = []
        
        for i, row in enumerate(data_rows, start=2):
            try:
                if len(row) < 6:
                    errors.append(f"Row {i}: Insufficient data")
                    continue
                
                # Parse row data
                transaction_id = int(row[0]) if row[0] else None
                transaction_date = self._parse_date(row[1])
                transaction_type = row[2].lower() if row[2] else None
                amount = float(row[3]) if row[3] else 0.0
                category_name = row[4]
                description = row[5]
                payment_method = row[6] if len(row) > 6 else "cash"
                is_savings_related = row[7] == "Yes" if len(row) > 7 else False
                savings_bundle_id = int(row[8]) if len(row) > 8 and row[8] else None
                project_id = int(row[9]) if len(row) > 9 and row[9] else None
                
                if not transaction_date or not transaction_type:
                    errors.append(f"Row {i}: Missing required fields (date or type)")
                    continue
                
                # Find or create category
                category = db.query(Category).filter(Category.name == category_name).first()
                if not category:
                    # Create default category
                    category_type = TransactionType.INCOME if transaction_type == "income" else TransactionType.EXPENSE
                    category = Category(
                        name=category_name or "Uncategorized",
                        type=category_type,
                        color="#6B7280",
                        icon="circle"
                    )
                    db.add(category)
                    db.flush()
                
                # Check if transaction exists
                existing = None
                if transaction_id:
                    existing = db.query(Transaction).filter(Transaction.id == transaction_id).first()
                
                if existing:
                    if conflict_strategy == "skip":
                        skipped += 1
                        continue
                    elif conflict_strategy == "update":
                        # Update existing
                        existing.date = transaction_date
                        existing.type = transaction_type
                        existing.amount = amount
                        existing.category_id = category.id
                        existing.description = description
                        existing.payment_method = payment_method
                        existing.is_savings_related = is_savings_related
                        existing.savings_bundle_id = savings_bundle_id
                        existing.project_id = project_id
                        imported += 1
                    elif conflict_strategy == "replace":
                        # Delete and recreate
                        db.delete(existing)
                        db.flush()
                        # Will create new below
                        existing = None
                
                if not existing:
                    # Create new transaction
                    new_transaction = Transaction(
                        date=transaction_date,
                        amount=amount,
                        type=transaction_type,
                        category_id=category.id,
                        description=description,
                        payment_method=payment_method,
                        is_savings_related=is_savings_related,
                        savings_bundle_id=savings_bundle_id,
                        project_id=project_id
                    )
                    db.add(new_transaction)
                    imported += 1
                
            except Exception as e:
                errors.append(f"Row {i}: {str(e)}")
        
        db.commit()
        return imported, skipped, errors
    
    def import_savings(self, db: Session, conflict_strategy: str = "skip") -> Tuple[int, int, List[str]]:
        """
        Import savings bundles from Google Sheets.
        
        Args:
            db: Database session
            conflict_strategy: "skip", "update", or "replace"
        
        Returns:
            Tuple of (imported_count, skipped_count, errors)
        """
        from app.models.database import SavingsType, SavingsStatus
        
        spreadsheet = self._get_spreadsheet()
        
        try:
            worksheet = spreadsheet.worksheet(self.SAVINGS_SHEET)
        except gspread.exceptions.WorksheetNotFound:
            return 0, 0, [f"Sheet '{self.SAVINGS_SHEET}' not found"]
        
        # Get all values (skip header)
        all_values = worksheet.get_all_values()
        if len(all_values) <= 1:
            return 0, 0, []
        
        data_rows = all_values[1:]
        imported = 0
        skipped = 0
        errors = []
        
        for i, row in enumerate(data_rows, start=2):
            try:
                if len(row) < 5:
                    errors.append(f"Row {i}: Insufficient data")
                    continue
                
                # Parse row data
                bundle_id = int(row[0]) if row[0] else None
                name = row[1]
                bank_name = row[2]
                bundle_type = row[3].lower().replace(" ", "_") if row[3] else None
                initial_deposit = float(row[4]) if row[4] else 0.0
                current_amount = float(row[5]) if len(row) > 5 and row[5] else initial_deposit
                future_amount = float(row[6]) if len(row) > 6 and row[6] else initial_deposit
                interest_rate = float(row[7]) if len(row) > 7 and row[7] else None
                start_date = self._parse_date(row[8]) if len(row) > 8 else None
                maturity_date = self._parse_date(row[9]) if len(row) > 9 else None
                status = row[10].lower() if len(row) > 10 and row[10] else "active"
                notes = row[11] if len(row) > 11 else None
                linked_project_id = int(row[12]) if len(row) > 12 and row[12] else None
                
                if not name or not bank_name:
                    errors.append(f"Row {i}: Missing required fields (name or bank_name)")
                    continue
                
                # Check if bundle exists
                existing = None
                if bundle_id:
                    existing = db.query(SavingsBundle).filter(SavingsBundle.id == bundle_id).first()
                
                if existing:
                    if conflict_strategy == "skip":
                        skipped += 1
                        continue
                    elif conflict_strategy == "update":
                        # Update existing
                        existing.name = name
                        existing.bank_name = bank_name
                        existing.type = bundle_type
                        existing.initial_deposit = initial_deposit
                        existing.current_amount = current_amount
                        existing.future_amount = future_amount
                        existing.interest_rate = interest_rate
                        existing.start_date = start_date
                        existing.maturity_date = maturity_date
                        existing.status = status
                        existing.notes = notes
                        existing.linked_project_id = linked_project_id
                        imported += 1
                    elif conflict_strategy == "replace":
                        db.delete(existing)
                        db.flush()
                        existing = None
                
                if not existing:
                    # Create new bundle
                    new_bundle = SavingsBundle(
                        name=name,
                        bank_name=bank_name,
                        type=bundle_type or SavingsType.SAVINGS_GOAL,
                        initial_deposit=initial_deposit,
                        current_amount=current_amount,
                        future_amount=future_amount,
                        interest_rate=interest_rate,
                        start_date=start_date or date.today(),
                        maturity_date=maturity_date,
                        status=status,
                        notes=notes,
                        linked_project_id=linked_project_id
                    )
                    db.add(new_bundle)
                    imported += 1
                
            except Exception as e:
                errors.append(f"Row {i}: {str(e)}")
        
        db.commit()
        return imported, skipped, errors


# Singleton instance
google_sheets_service = GoogleSheetsService()
