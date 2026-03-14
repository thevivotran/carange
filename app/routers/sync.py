from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Optional
from pydantic import BaseModel
from datetime import date

from app.models.database import get_db
from app.services.google_sheets import google_sheets_service

router = APIRouter()


# Request/Response Schemas
class ExportRequest(BaseModel):
    data_type: str = "all"  # "all", "transactions", "savings"
    clear_first: bool = True
    start_date: Optional[date] = None
    end_date: Optional[date] = None


class ImportRequest(BaseModel):
    data_type: str = "all"  # "all", "transactions", "savings"
    conflict_strategy: str = "skip"  # "skip", "update", "replace"


class SyncResponse(BaseModel):
    success: bool
    message: str
    transactions_exported: Optional[int] = None
    transactions_imported: Optional[int] = None
    savings_exported: Optional[int] = None
    savings_imported: Optional[int] = None
    skipped: Optional[int] = None
    errors: Optional[list] = None
    spreadsheet_url: Optional[str] = None


class StatusResponse(BaseModel):
    success: bool
    message: str
    spreadsheet_title: Optional[str] = None
    spreadsheet_url: Optional[str] = None
    available_sheets: Optional[list] = None
    error: Optional[str] = None


@router.get("/google-sheets/status", response_model=StatusResponse)
def check_google_sheets_status():
    """Check Google Sheets connection status."""
    try:
        result = google_sheets_service.test_connection()
        return StatusResponse(
            success=result["success"],
            message=result["message"],
            spreadsheet_title=result.get("spreadsheet_title"),
            spreadsheet_url=result.get("spreadsheet_url"),
            available_sheets=result.get("available_sheets"),
            error=result.get("error")
        )
    except Exception as e:
        return StatusResponse(
            success=False,
            message="Connection check failed",
            error=str(e)
        )


@router.post("/google-sheets/export", response_model=SyncResponse)
def export_to_google_sheets(
    request: ExportRequest,
    db: Session = Depends(get_db)
):
    """
    Export transactions and/or savings bundles to Google Sheets.
    
    - **data_type**: "all", "transactions", or "savings"
    - **clear_first**: If True, clears existing data before export
    - **start_date**: Optional filter for transactions (inclusive)
    - **end_date**: Optional filter for transactions (inclusive)
    """
    try:
        transactions_count = 0
        savings_count = 0
        
        # Export transactions
        if request.data_type in ["all", "transactions"]:
            transactions_count = google_sheets_service.export_transactions(
                db, 
                clear_first=request.clear_first and request.data_type != "savings"
            )
        
        # Export savings bundles
        if request.data_type in ["all", "savings"]:
            savings_count = google_sheets_service.export_savings(
                db,
                clear_first=request.clear_first and request.data_type != "transactions"
            )
        
        # Get spreadsheet URL
        status = google_sheets_service.test_connection()
        spreadsheet_url = status.get("spreadsheet_url", "")
        
        return SyncResponse(
            success=True,
            message="Export completed successfully",
            transactions_exported=transactions_count,
            savings_exported=savings_count,
            spreadsheet_url=spreadsheet_url
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Export failed: {str(e)}")


@router.post("/google-sheets/import", response_model=SyncResponse)
def import_from_google_sheets(
    request: ImportRequest,
    db: Session = Depends(get_db)
):
    """
    Import transactions and/or savings bundles from Google Sheets.
    
    - **data_type**: "all", "transactions", or "savings"
    - **conflict_strategy**: How to handle existing records:
      - "skip": Skip records with matching IDs (default)
      - "update": Update existing records
      - "replace": Delete existing records and create new ones
    """
    try:
        transactions_imported = 0
        transactions_skipped = 0
        savings_imported = 0
        savings_skipped = 0
        all_errors = []
        
        # Import transactions
        if request.data_type in ["all", "transactions"]:
            t_imported, t_skipped, t_errors = google_sheets_service.import_transactions(
                db,
                conflict_strategy=request.conflict_strategy
            )
            transactions_imported = t_imported
            transactions_skipped = t_skipped
            all_errors.extend(t_errors)
        
        # Import savings bundles
        if request.data_type in ["all", "savings"]:
            s_imported, s_skipped, s_errors = google_sheets_service.import_savings(
                db,
                conflict_strategy=request.conflict_strategy
            )
            savings_imported = s_imported
            savings_skipped = s_skipped
            all_errors.extend(s_errors)
        
        total_imported = transactions_imported + savings_imported
        total_skipped = transactions_skipped + savings_skipped
        
        return SyncResponse(
            success=True,
            message=f"Import completed. {total_imported} records imported, {total_skipped} skipped.",
            transactions_imported=transactions_imported,
            savings_imported=savings_imported,
            skipped=total_skipped,
            errors=all_errors if all_errors else None
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Import failed: {str(e)}")


@router.post("/google-sheets/clear")
def clear_google_sheets(
    data_type: str = "all",  # "all", "transactions", "savings"
):
    """
    Clear data from Google Sheets.
    
    - **data_type**: "all", "transactions", or "savings"
    """
    try:
        cleared_sheets = []
        
        if data_type in ["all", "transactions"]:
            if google_sheets_service.clear_sheet("Transactions"):
                cleared_sheets.append("Transactions")
        
        if data_type in ["all", "savings"]:
            if google_sheets_service.clear_sheet("Savings Bundles"):
                cleared_sheets.append("Savings Bundles")
        
        return {
            "success": True,
            "message": f"Cleared sheets: {', '.join(cleared_sheets)}",
            "cleared_sheets": cleared_sheets
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Clear failed: {str(e)}")


@router.get("/google-sheets/url")
def get_google_sheets_url():
    """Get the Google Sheets URL for manual access."""
    try:
        status = google_sheets_service.test_connection()
        if status["success"]:
            return {
                "success": True,
                "url": status["spreadsheet_url"],
                "title": status.get("spreadsheet_title", "")
            }
        else:
            return {
                "success": False,
                "error": status.get("error", "Unknown error"),
                "message": status.get("message", "Failed to get URL")
            }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "message": "Failed to get Google Sheets URL"
        }
