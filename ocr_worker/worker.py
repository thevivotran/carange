"""
OCR worker — processes import_jobs one at a time.

On PostgreSQL (production): uses LISTEN/NOTIFY for instant wake-up when a new
job is uploaded, plus SELECT FOR UPDATE SKIP LOCKED for safe concurrent claiming.
On SQLite (development): falls back to polling every POLL_INTERVAL seconds.

Environment variables:
  DATABASE_URL    connection string      default: sqlite:///./carange.db
  UPLOAD_DIR      where images live      default: uploads
  POLL_INTERVAL   seconds between polls  default: 10  (SQLite only)
  STUCK_TIMEOUT   minutes before a PROCESSING job is reclaimed  default: 30
  MAX_RETRIES     attempts before permanent failure              default: 3
"""

import logging
import os
import pathlib
import select
import threading
import time
from datetime import datetime, timedelta, timezone

LIVENESS_FILE = pathlib.Path("/tmp/worker_alive")

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker, Session

from app.models.database import ImportJob, ImportJobStatus

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
log = logging.getLogger("ocr_worker")

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./carange.db")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "10"))
STUCK_TIMEOUT_MINUTES = int(os.getenv("STUCK_TIMEOUT", "30"))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))

_IS_PG = DATABASE_URL.startswith("postgresql") or DATABASE_URL.startswith("postgres")


# ── Liveness heartbeat ────────────────────────────────────────────────────────
#
# A single job can legitimately block for many minutes (a cold Ollama vision
# call has a 600 s budget), but the k8s liveness probe requires LIVENESS_FILE
# to be fresher than 5 minutes. A heartbeat thread keeps touching the file as
# long as the main loop has made progress within STUCK_TIMEOUT — so a hung
# loop still fails the probe, while a slow job doesn't get the pod killed.

_HEARTBEAT_INTERVAL = 30.0
_last_progress = time.monotonic()


def _mark_progress() -> None:
    global _last_progress
    _last_progress = time.monotonic()


def _start_heartbeat() -> None:
    def beat():
        while True:
            if time.monotonic() - _last_progress < STUCK_TIMEOUT_MINUTES * 60:
                LIVENESS_FILE.touch()
            time.sleep(_HEARTBEAT_INTERVAL)

    threading.Thread(target=beat, daemon=True, name="liveness-heartbeat").start()


def _psycopg2_dsn(url: str) -> str:
    """Strip SQLAlchemy dialect prefix so psycopg2 can parse the URL."""
    return url.replace("postgresql+psycopg2://", "postgresql://").replace("postgres://", "postgresql://")


def _make_session_factory():
    _is_sqlite = DATABASE_URL.startswith("sqlite")
    engine = create_engine(
        DATABASE_URL,
        connect_args={"check_same_thread": False} if _is_sqlite else {},
        pool_pre_ping=not _is_sqlite,
    )

    if _is_sqlite:

        @event.listens_for(engine, "connect")
        def set_sqlite_pragma(dbapi_conn, _):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.close()

    return sessionmaker(bind=engine, autocommit=False, autoflush=False)


# ── Job claiming ──────────────────────────────────────────────────────────────


def _cleanup_job_file(file_path: str | None) -> None:
    """Best-effort removal of a job's uploaded image."""
    if not file_path:
        return
    from ocr_worker.processor import _resolve_file_path

    full_path = _resolve_file_path(file_path)
    if os.path.isfile(full_path):
        try:
            os.remove(full_path)
        except OSError as exc:
            log.warning("Could not delete image %s: %s", full_path, exc)


def _claim_next_pg(db: Session) -> ImportJob | None:
    """PostgreSQL: atomic claim using SELECT FOR UPDATE SKIP LOCKED.

    Also reclaims jobs stuck in PROCESSING based on started_at, then claims
    the oldest PENDING job not waiting for a retry_after backoff.
    """
    now = datetime.now(timezone.utc)
    stuck_cutoff = now - timedelta(minutes=STUCK_TIMEOUT_MINUTES)

    # Each stuck reclaim consumes a retry so a poison-pill job that hangs or
    # crashes the worker can't loop forever.
    failed = db.execute(
        text("""
            UPDATE import_jobs
               SET status = 'failed', started_at = NULL, processed_at = :now,
                   error_message = 'Permanent failure: stuck in processing after max retries'
             WHERE status = 'processing'
               AND started_at < :cutoff
               AND retry_count >= :max_retries
             RETURNING id, file_path
        """),
        {"now": now, "cutoff": stuck_cutoff, "max_retries": MAX_RETRIES},
    ).fetchall()
    for job_id, file_path in failed:
        log.error("Job %d FAILED permanently: stuck in processing after %d retries", job_id, MAX_RETRIES)
        _cleanup_job_file(file_path)

    db.execute(
        text("""
            UPDATE import_jobs
               SET status = 'pending', started_at = NULL,
                   retry_count = COALESCE(retry_count, 0) + 1
             WHERE status = 'processing'
               AND started_at < :cutoff
        """),
        {"cutoff": stuck_cutoff},
    )

    row = db.execute(
        text("""
            UPDATE import_jobs
               SET status = 'processing', started_at = :now
             WHERE id = (
                 SELECT id FROM import_jobs
                  WHERE status = 'pending'
                    AND (retry_after IS NULL OR retry_after <= :now)
                  ORDER BY created_at ASC
                  FOR UPDATE SKIP LOCKED
                  LIMIT 1
             )
             RETURNING id
        """),
        {"now": now},
    ).fetchone()

    db.commit()

    if row is None:
        return None

    return db.query(ImportJob).filter(ImportJob.id == row[0]).first()


def _claim_next_sqlite(db: Session) -> ImportJob | None:
    """SQLite: two-step select-then-update (safe for single-worker deployment)."""
    now = datetime.now(timezone.utc)
    stuck_cutoff = now - timedelta(minutes=STUCK_TIMEOUT_MINUTES)

    stuck_jobs = (
        db.query(ImportJob)
        .filter(
            ImportJob.status == ImportJobStatus.PROCESSING,
            ImportJob.started_at < stuck_cutoff,
        )
        .all()
    )
    for stuck in stuck_jobs:
        # Each stuck reclaim consumes a retry (see _claim_next_pg)
        if (stuck.retry_count or 0) >= MAX_RETRIES:
            log.error("Job %d FAILED permanently: stuck in processing after %d retries", stuck.id, MAX_RETRIES)
            stuck.status = ImportJobStatus.FAILED
            stuck.started_at = None
            stuck.processed_at = now
            stuck.error_message = "Permanent failure: stuck in processing after max retries"
            _cleanup_job_file(stuck.file_path)
        else:
            log.warning("Reclaiming stuck job %d (PROCESSING since %s)", stuck.id, stuck.started_at)
            stuck.status = ImportJobStatus.PENDING
            stuck.started_at = None
            stuck.retry_count = (stuck.retry_count or 0) + 1
    if stuck_jobs:
        db.commit()

    job = (
        db.query(ImportJob)
        .filter(
            ImportJob.status == ImportJobStatus.PENDING,
            (ImportJob.retry_after == None) | (ImportJob.retry_after <= now),  # noqa: E711
        )
        .order_by(ImportJob.created_at)
        .first()
    )
    if not job:
        return None

    job.status = ImportJobStatus.PROCESSING
    job.started_at = now
    db.commit()
    db.refresh(job)
    return job


# ── Failure handling with exponential backoff ─────────────────────────────────


def _handle_failure(db: Session, job: ImportJob, reason: str) -> None:
    """Retry up to MAX_RETRIES with exponential backoff; then permanently fail."""
    retry_count = (job.retry_count or 0) + 1
    if retry_count <= MAX_RETRIES:
        backoff_secs = (2 ** (retry_count - 1)) * 60  # 1 min, 2 min, 4 min
        job.status = ImportJobStatus.PENDING
        job.started_at = None
        job.retry_count = retry_count
        job.retry_after = datetime.now(timezone.utc) + timedelta(seconds=backoff_secs)
        job.error_message = f"Retry {retry_count}/{MAX_RETRIES}: {reason}"
        db.commit()
        log.warning(
            "Job %d → retry %d/%d in %ds: %s",
            job.id,
            retry_count,
            MAX_RETRIES,
            backoff_secs,
            reason,
        )
    else:
        job.status = ImportJobStatus.FAILED
        job.started_at = None
        job.error_message = f"Permanent failure after {MAX_RETRIES} retries. Last: {reason}"
        job.processed_at = datetime.now(timezone.utc)
        db.commit()
        _cleanup_job_file(job.file_path)
        log.error("Job %d FAILED permanently after %d retries: %s", job.id, MAX_RETRIES, reason)


# ── Run loops ─────────────────────────────────────────────────────────────────


def _process_one(SessionFactory, claim_fn) -> bool:
    """Claim and process one job. Returns True if a job was processed."""
    from ocr_worker.processor import TransientJobError, process_job

    with SessionFactory() as db:
        job = claim_fn(db)
        if job is None:
            return False

        log.info("Claimed job %d (%s)", job.id, job.filename)
        _mark_progress()
        try:
            process_job(job, db)
        except TransientJobError as exc:
            log.warning("Transient error in job %d: %s", job.id, exc)
            _retry_in_fresh_session(SessionFactory, job.id, str(exc))
        except Exception as exc:
            log.exception("Unhandled error in job %d", job.id)
            _retry_in_fresh_session(SessionFactory, job.id, f"Internal error: {exc}")
        _mark_progress()
    return True


def _retry_in_fresh_session(SessionFactory, job_id: int, reason: str) -> None:
    """Schedule a retry using a clean session (the job's own may be poisoned)."""
    with SessionFactory() as recovery_db:
        failed = recovery_db.query(ImportJob).filter(ImportJob.id == job_id).first()
        if failed:
            _handle_failure(recovery_db, failed, reason)


def _drain_queue(SessionFactory, claim_fn) -> None:
    """Process all available jobs until the queue is empty."""
    while _process_one(SessionFactory, claim_fn):
        pass


def _run_postgres(SessionFactory) -> None:
    """PostgreSQL mode: LISTEN 'ocr_jobs' + FOR UPDATE SKIP LOCKED."""
    import psycopg2
    import psycopg2.extensions

    dsn = _psycopg2_dsn(DATABASE_URL)

    def _make_listen_conn():
        conn = psycopg2.connect(dsn)
        conn.set_isolation_level(psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT)
        conn.cursor().execute("LISTEN ocr_jobs")
        return conn

    log.info("OCR worker: LISTEN/NOTIFY mode (PostgreSQL)")
    listen_conn = _make_listen_conn()

    while True:
        _mark_progress()

        # Drain any jobs that are already pending (handles backlog on startup
        # and jobs queued while the listen connection was being (re)established).
        _drain_queue(SessionFactory, _claim_next_pg)

        # Block until a pg_notify arrives or the 30 s fallback fires.
        # The fallback catches notifications missed during a reconnect and also
        # reclaims stuck jobs on every timeout tick.
        try:
            readable, _, _ = select.select([listen_conn], [], [], 30.0)
            if readable:
                listen_conn.poll()
                listen_conn.notifies.clear()
        except Exception as exc:
            log.warning("LISTEN connection error (%s) — reconnecting in 5s", exc)
            try:
                listen_conn.close()
            except Exception:
                pass
            time.sleep(5)
            try:
                listen_conn = _make_listen_conn()
                log.info("LISTEN connection restored")
            except Exception as reconnect_exc:
                log.error("Reconnect failed: %s — will retry next loop", reconnect_exc)


def _run_sqlite(SessionFactory) -> None:
    """SQLite mode: poll every POLL_INTERVAL seconds (development only)."""
    log.info("OCR worker: polling mode (SQLite, interval=%ds)", POLL_INTERVAL)
    while True:
        _drain_queue(SessionFactory, _claim_next_sqlite)
        log.debug("Queue empty — sleeping %ds", POLL_INTERVAL)
        _mark_progress()
        time.sleep(POLL_INTERVAL)


def run() -> None:
    log.info("OCR worker starting — %s", DATABASE_URL)
    LIVENESS_FILE.touch()
    _start_heartbeat()
    SessionFactory = _make_session_factory()

    if _IS_PG:
        _run_postgres(SessionFactory)
    else:
        _run_sqlite(SessionFactory)


if __name__ == "__main__":
    run()
