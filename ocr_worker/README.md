# OCR Worker

Processes screenshot uploads (`ImportJob` rows) into transactions and feeds them into
Carange's review pipeline. Runs as a separate container
(`ghcr.io/thevivotran/carange-ocr-worker`) alongside the main app — kept out of the lean app
image because of its heavy PaddleOCR/OpenCV dependencies.

---

## How it works

For each claimed `ImportJob`, `process_job` (`processor.py`) tries two extraction paths in
order:

1. **Ollama vision** (`Qwen3.5-9B`, self-hosted) — if `OLLAMA_URL` is configured, the image
   is sent straight to the vision model with a prompt asking for a JSON array of
   `{date, amount, type, description, category_hint}`. Handles any screenshot layout without
   a dedicated parser. Confidence is fixed at `VISION_CONFIDENCE = 0.85`. Responses are
   validated item-by-item (type must be `expense`/`income`, amounts must be positive;
   VND-formatted string amounts like `"45.000đ"` are coerced correctly). An explicit empty
   array is trusted as "no transactions" and finishes the job — only a *failed* extraction
   falls through to path 2.
2. **PaddleOCR + source-specific parser** (`ocr.py` + `parsers/`) — fallback when Ollama is
   offline or returns nothing parseable:
   - `ocr.extract_blocks` runs PaddleOCR (Vietnamese model, GPU auto-detected) and returns a
     flat list of `TextBlock`s with text, confidence, and bounding box.
   - `source_detector.detect_source` keyword-matches the extracted text against weighted
     rules to guess the source app (Timo / Shopee / Grab), unless `job.source_hint` was set
     at upload time.
   - `parsers.get_parser` returns the matching parser (`TimoParser`, `ShopeeParser`,
     `GrabParser`, or `GenericParser` as fallback), which turns the text blocks into
     `ParsedTransaction`s using each app's layout conventions.

Either path's results are converted to `IngestItem`s and run through the same dedup → rules
→ review pipeline as email imports (`app.services.ingest_service.commit_ingest_batch`),
landing in the Review Inbox unless confidence is high enough to auto-approve. The uploaded
image is deleted from disk once the job finishes (success or failure).

### Job claiming

The worker supports two database backends with different claiming strategies:

- **PostgreSQL** (production) — `LISTEN/NOTIFY` on the `ocr_jobs` channel for instant
  wake-up on upload, plus atomic `SELECT ... FOR UPDATE SKIP LOCKED` so multiple replicas can
  claim jobs safely without colliding. Falls back to a 30 s poll to catch missed
  notifications and reclaim stuck jobs.
- **SQLite** (development / single-instance self-hosting) — simple poll every
  `POLL_INTERVAL` seconds with a select-then-update claim (safe for a single worker only).

In both modes, jobs stuck in `PROCESSING` past `STUCK_TIMEOUT` minutes (e.g. from a crashed
run) are reclaimed back to `PENDING`. Each reclaim consumes a retry, so a poison-pill job
that hangs or crashes the worker is permanently failed after `MAX_RETRIES` instead of
looping forever.

### Retry & failure handling

Transient failures (OCR engine errors, unexpected exceptions) retry with exponential
backoff (1 min, 2 min, 4 min, ... up to `MAX_RETRIES`), tracked via `retry_after` /
`retry_count` on the `ImportJob` row; after the limit the job is marked permanently
`FAILED`. Permanent failures (missing image, parser bugs) fail immediately. The uploaded
image is kept on disk while retries remain and deleted once the job reaches a terminal
state (`DONE` or `FAILED`).

A liveness file at `/tmp/worker_alive` is touched every 30 s by a heartbeat thread as long
as the main loop has made progress within `STUCK_TIMEOUT` — so a single long job (a cold
vision call can block for up to 10 min) doesn't trip the container health check, while a
genuinely hung loop still does.

---

## Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `DATABASE_URL` | `sqlite:///./carange.db` | Same database as the main app — backend is auto-detected from the URL scheme |
| `UPLOAD_DIR` | `uploads` | Where uploaded screenshots are stored |
| `POLL_INTERVAL` | `10` | Seconds between polls (SQLite mode only) |
| `STUCK_TIMEOUT` | `30` | Minutes before a `PROCESSING` job is reclaimed |
| `MAX_RETRIES` | `3` | Retry attempts (exponential backoff) before permanent failure |
| `OLLAMA_URL` | — | Self-hosted Ollama endpoint for vision extraction (set on the main app — read via `app.services.ollama`) |

---

## Running

Enabled by uncommenting the `ocr_worker` service in `docker-compose.yml` /
`docker-compose.pg.yml` — see the **Self-Hosting** section of the
[main README](../README.md).

Locally:

```bash
cd carange_app/carange
python -m ocr_worker.worker
```

It shares the main app's database and `requirements.txt`; worker-specific dependencies
(`paddlepaddle`, `paddleocr`, `opencv-python-headless`, ...) live in
`ocr_worker/requirements.txt` and are installed in a separate Docker layer to keep the app
image lean. The Dockerfile sets `FLAGS_use_mkldnn=0` to avoid a known double-free crash on
non-AVX2 hosts (e.g. CI runners).
