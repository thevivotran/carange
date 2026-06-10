from collections import OrderedDict
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models.database import EmailIngestLog, ImportJob, ImportJobStatus, ImportSource, Transaction, get_db
from app.routers.fragments._helpers import render_fragment
from app.services.settings_service import get_setting

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


def _group_by_date(items, date_attr: str = "created_at"):
    today = date.today()
    yesterday = today - timedelta(days=1)
    groups: OrderedDict = OrderedDict()
    for item in items:
        dt = getattr(item, date_attr, None)
        if dt is None:
            label = "Unknown"
        else:
            # Convert timezone-aware datetimes to local time before date comparison
            # so items created "today" locally are never shown as "Yesterday" (UTC off-by-one)
            if hasattr(dt, "tzinfo") and dt.tzinfo is not None:
                dt = dt.astimezone()
            d = dt.date() if hasattr(dt, "date") else dt
            if d == today:
                label = "Today"
            elif d == yesterday:
                label = "Yesterday"
            else:
                label = d.strftime("%d %b %Y")
        groups.setdefault(label, []).append(item)
    return list(groups.items())


@router.get("/jobs")
def fragment_import_jobs(
    request: Request,
    status: Optional[str] = None,
    search: Optional[str] = None,
    db: Session = Depends(get_db),
):
    query = db.query(ImportJob).order_by(ImportJob.created_at.desc())
    if status and status != "all":
        try:
            query = query.filter(ImportJob.status == ImportJobStatus(status))
        except ValueError:
            pass
    if search:
        query = query.filter(ImportJob.filename.ilike(f"%{search}%"))
    jobs = query.limit(100).all()

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
            "grouped_jobs": _group_by_date(jobs, "created_at"),
            "has_active": has_active,
            "status_filter": status or "all",
            "search": search or "",
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


def _email_worker_stats(db: Session) -> dict:
    """Worker health + 7-day ingest counters for the Email Receipts panel."""
    week_ago = datetime.now(timezone.utc) - timedelta(days=7)
    counts = dict(
        db.query(EmailIngestLog.status, func.count(EmailIngestLog.id))
        .filter(EmailIngestLog.created_at >= week_ago)
        .group_by(EmailIngestLog.status)
        .all()
    )

    last_ok_raw = get_setting(db, "email_worker_last_ok")
    last_error = get_setting(db, "email_worker_last_error") or ""
    last_ok = None
    online = False
    if last_ok_raw:
        try:
            last_ok = datetime.fromisoformat(last_ok_raw)
            if last_ok.tzinfo is None:
                last_ok = last_ok.replace(tzinfo=timezone.utc)
            online = datetime.now(timezone.utc) - last_ok < timedelta(minutes=10)
        except ValueError:
            pass

    return {
        "done": counts.get("done", 0),
        "pending": counts.get("pending", 0),
        "failed": counts.get("failed", 0),
        "last_ok": last_ok,
        "last_error": last_error,
        "online": online,
    }


def _render_email_logs(
    request: Request,
    db: Session,
    status: Optional[str],
    search: Optional[str],
    toast: Optional[str] = None,
):
    query = db.query(EmailIngestLog).order_by(EmailIngestLog.created_at.desc())
    if status and status != "all":
        query = query.filter(EmailIngestLog.status == status)
    if search:
        query = query.filter(
            or_(
                EmailIngestLog.subject.ilike(f"%{search}%"),
                EmailIngestLog.sender.ilike(f"%{search}%"),
            )
        )
    logs = query.limit(100).all()
    return render_fragment(
        request,
        "partials/import/_email_logs.html",
        {
            "logs": logs,
            "grouped_logs": _group_by_date(logs, "created_at"),
            "status_filter": status or "all",
            "search": search or "",
            "worker_stats": _email_worker_stats(db),
        },
        toast=toast,
    )


@router.get("/email-logs")
def fragment_email_logs(
    request: Request,
    status: Optional[str] = None,
    search: Optional[str] = None,
    db: Session = Depends(get_db),
):
    return _render_email_logs(request, db, status, search)


@router.post("/email-logs/{log_id}/reprocess")
def reprocess_email_log(
    request: Request,
    log_id: int,
    status: Optional[str] = None,
    search: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Queue a failed / zero-transaction email for reprocessing from its stored raw copy."""
    row = db.get(EmailIngestLog, log_id)
    toast = "Email not found"
    if row is not None:
        replayable = row.status == "failed" or (row.status == "done" and not row.transaction_count)
        if not replayable:
            toast = "Only failed or zero-transaction emails can be reprocessed"
        elif not row.raw_size:
            toast = "No stored copy of this email to reprocess"
        else:
            row.status = "pending"
            row.retry_count = 0
            row.retry_after = datetime.now(timezone.utc)
            row.error_message = None
            row.processed_at = None
            db.commit()
            toast = "Email queued — the worker will reprocess it within a minute"
    return _render_email_logs(request, db, status, search, toast=toast)
