"""
OCR worker — polls import_jobs for pending work and processes one job at a time.

Environment variables:
  DATABASE_URL   SQLite path              default: sqlite:///./carange.db
  UPLOAD_DIR     Where images are stored  default: uploads
  POLL_INTERVAL  Seconds between polls    default: 10
"""

import logging
import os
import pathlib
import time
from datetime import datetime, timezone

LIVENESS_FILE = pathlib.Path("/tmp/worker_alive")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

from app.models.database import ImportJob, ImportJobStatus

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
log = logging.getLogger("ocr_worker")

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./carange.db")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "10"))


def _make_session_factory():
    engine = create_engine(
        DATABASE_URL,
        connect_args={"check_same_thread": False},
    )
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)


def _claim_next(db: Session) -> ImportJob | None:
    """
    Atomically claim the oldest pending job.
    SQLite doesn't support SELECT … FOR UPDATE SKIP LOCKED, so we rely on
    WAL-mode serialised writes and single-worker deployment for safety.
    """
    job = db.query(ImportJob).filter(ImportJob.status == ImportJobStatus.PENDING).order_by(ImportJob.created_at).first()
    if not job:
        return None

    job.status = ImportJobStatus.PROCESSING
    db.commit()
    db.refresh(job)
    return job


def _mark_failed(db: Session, job: ImportJob, reason: str) -> None:
    job.status = ImportJobStatus.FAILED
    job.error_message = reason
    job.processed_at = datetime.now(timezone.utc)
    db.commit()
    log.warning("Job %d FAILED: %s", job.id, reason)


def run() -> None:
    from ocr_worker.processor import process_job

    log.info("OCR worker starting — %s", DATABASE_URL)
    SessionFactory = _make_session_factory()

    while True:
        with SessionFactory() as db:
            job = _claim_next(db)

            if job is None:
                log.debug("Queue empty — sleeping %ds", POLL_INTERVAL)
                LIVENESS_FILE.touch()
                time.sleep(POLL_INTERVAL)
                continue

            log.info("Claimed job %d (%s)", job.id, job.filename)
            LIVENESS_FILE.touch()
            try:
                process_job(job, db)
            except Exception as exc:
                log.exception("Unhandled error in job %d", job.id)
                # Re-fetch in case the session is dirty after the exception
                with SessionFactory() as recovery_db:
                    failed_job = recovery_db.query(ImportJob).filter(ImportJob.id == job.id).first()
                    if failed_job:
                        _mark_failed(recovery_db, failed_job, f"Internal error: {exc}")


if __name__ == "__main__":
    run()
