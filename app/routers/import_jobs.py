import hashlib
import os
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.models.database import ImportJob, ImportJobStatus, ImportSource, get_db
from app.models.schemas import ImportJob as ImportJobSchema
from app.models.schemas import ImportJobUpdate

UPLOAD_DIR = os.getenv("UPLOAD_DIR", "uploads")

router = APIRouter()

ALLOWED_MIME_TYPES = {"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"}
MAX_FILE_SIZE = 20 * 1024 * 1024  # 20 MB


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_source(value: Optional[str]) -> Optional[ImportSource]:
    if not value:
        return None
    try:
        return ImportSource(value)
    except ValueError:
        return None


@router.post("/jobs", response_model=List[ImportJobSchema])
async def create_import_jobs(
    files: List[UploadFile] = File(...),
    source_hint: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    hint = _safe_source(source_hint)
    results: List[ImportJob] = []

    for upload in files:
        if upload.content_type and upload.content_type not in ALLOWED_MIME_TYPES:
            raise HTTPException(
                status_code=415,
                detail=f"{upload.filename}: unsupported file type '{upload.content_type}'",
            )

        raw = await upload.read()
        if len(raw) > MAX_FILE_SIZE:
            raise HTTPException(status_code=413, detail=f"{upload.filename}: file exceeds 20 MB limit")

        digest = _sha256(raw)

        # Deduplication — return existing job instead of creating a duplicate
        existing = db.query(ImportJob).filter(ImportJob.image_hash == digest).first()
        if existing:
            results.append(existing)
            continue

        # Persist file
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        ext = os.path.splitext(upload.filename or "image")[1] or ".jpg"
        filename = f"{digest}{ext}"
        file_path = os.path.join(UPLOAD_DIR, filename)
        with open(file_path, "wb") as fh:
            fh.write(raw)

        job = ImportJob(
            filename=upload.filename or filename,
            file_path=file_path,
            image_hash=digest,
            source_hint=hint,
            status=ImportJobStatus.PENDING,
        )
        db.add(job)
        db.flush()
        results.append(job)

    db.commit()
    for job in results:
        db.refresh(job)
    return results


@router.get("/jobs", response_model=List[ImportJobSchema])
def list_import_jobs(
    status: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    query = db.query(ImportJob)
    if status:
        try:
            query = query.filter(ImportJob.status == ImportJobStatus(status))
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid status '{status}'")
    return query.order_by(ImportJob.created_at.desc()).offset(skip).limit(limit).all()


@router.get("/jobs/{job_id}", response_model=ImportJobSchema)
def get_import_job(job_id: int, db: Session = Depends(get_db)):
    job = db.query(ImportJob).filter(ImportJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Import job not found")
    return job


@router.patch("/jobs/{job_id}", response_model=ImportJobSchema)
def update_import_job(job_id: int, payload: ImportJobUpdate, db: Session = Depends(get_db)):
    """Used by the OCR worker to report progress and results."""
    job = db.query(ImportJob).filter(ImportJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Import job not found")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(job, field, value)

    if payload.status in (ImportJobStatus.DONE, ImportJobStatus.FAILED) and not job.processed_at:
        job.processed_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(job)
    return job


@router.delete("/jobs/{job_id}")
def delete_import_job(job_id: int, db: Session = Depends(get_db)):
    job = db.query(ImportJob).filter(ImportJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Import job not found")

    if job.file_path and os.path.isfile(job.file_path):
        os.remove(job.file_path)

    db.delete(job)
    db.commit()
    return {"message": "Import job deleted"}
