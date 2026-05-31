from typing import Optional

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.models.database import EmailIngestLog, ImportJob, ImportJobStatus, ImportSource, Transaction, get_db
from app.routers.fragments._helpers import render_fragment

router = APIRouter()


def _humanize_error(msg: str) -> str:
    if not msg:
        return ""
    m = msg.lower()
    if "date" in m or "ngày" in m:
        return "Couldn't read the transaction date. Try a clearer screenshot."
    if any(w in m for w in ("amount", "price", "money", "số tiền")):
        return "Couldn't read the amount. Try cropping closer to the numbers."
    if "timeout" in m or "timed out" in m:
        return "Processing timed out. Try again or add the transaction manually."
    if any(w in m for w in ("unsupported", "format", "layout")):
        return "Image layout not recognised. Try setting the source hint manually."
    return "Processing failed. Try a clearer image or add the transaction manually."


@router.get("/jobs")
def fragment_import_jobs(
    request: Request,
    status: Optional[str] = None,
    db: Session = Depends(get_db),
):
    query = db.query(ImportJob).order_by(ImportJob.created_at.desc())
    if status and status != "all":
        try:
            query = query.filter(ImportJob.status == ImportJobStatus(status))
        except ValueError:
            pass
    jobs = query.limit(50).all()

    for job in jobs:
        if job.source_hint and isinstance(job.source_hint, ImportSource):
            job.source_hint = job.source_hint.value
        if job.detected_source and isinstance(job.detected_source, ImportSource):
            job.detected_source = job.detected_source.value

    has_active = any(j.status in (ImportJobStatus.PENDING, ImportJobStatus.PROCESSING) for j in jobs)

    return render_fragment(
        request,
        "partials/import/_job_list.html",
        {
            "jobs": jobs,
            "has_active": has_active,
            "status_filter": status or "all",
            "humanize_error": _humanize_error,
            "ImportJobStatus": ImportJobStatus,
        },
    )


@router.get("/{job_id}/transactions")
def fragment_job_transactions(
    request: Request,
    job_id: int,
    db: Session = Depends(get_db),
):
    txs = (
        db.query(Transaction)
        .filter(Transaction.import_job_id == job_id)
        .order_by(Transaction.date.desc())
        .limit(50)
        .all()
    )
    needs_review_count = sum(1 for t in txs if t.needs_review)
    scores = [t.confidence_score for t in txs if t.confidence_score is not None]
    avg_conf = sum(scores) / len(scores) if scores else None

    return render_fragment(
        request,
        "partials/import/_transactions.html",
        {
            "txs": txs,
            "job_id": job_id,
            "needs_review_count": needs_review_count,
            "avg_conf": avg_conf,
        },
    )


@router.get("/email-logs")
def fragment_email_logs(
    request: Request,
    db: Session = Depends(get_db),
):
    logs = db.query(EmailIngestLog).order_by(EmailIngestLog.created_at.desc()).limit(50).all()
    return render_fragment(
        request,
        "partials/import/_email_logs.html",
        {"logs": logs},
    )
