# Email Worker

Watches a Gmail IMAP inbox for forwarded bank/payment notification emails, parses them into
transactions, and feeds them into Carange's review pipeline. Runs as a separate container
(`ghcr.io/thevivotran/carange-email-worker`) alongside the main app.

---

## How it works

1. **UID-cursor ingestion** — new messages are discovered by UID, not by the `\\Seen` flag.
   The worker keeps a per-`(account, folder)` high-water mark in the `imap_folder_state`
   table and searches `UID <last+1>:*` each cycle, so reading the mailbox from another
   client can never starve the worker. The cursor resets automatically when the server
   reports a new `UIDVALIDITY` (Message-ID dedup prevents double ingestion).

2. **Push, not poll** — when the server supports IMAP `IDLE` (Gmail does), new mail is
   processed within seconds of arriving. Without IDLE the worker falls back to sleeping
   `POLL_INTERVAL` between cycles. Bodies are fetched with `BODY.PEEK[]`, which never
   flips `\\Seen` as a side effect (messages are marked seen explicitly, as a courtesy).

3. **Raw copy stored for replay** — each new message is recorded in `EmailIngestLog`
   (keyed by `Message-ID`) with its zlib-compressed RFC 2822 source attached. Retries and
   manual reprocessing replay from this stored copy; the IMAP message is never needed
   twice. Once transactions are committed the blob is cleared — failed and
   zero-transaction rows keep it so they can be replayed after a parser fix via the
   **Reprocess** button in Import → Email Receipts.

4. Extracts MIME parts (`email_parser.py`) — plaintext, HTML, sender, subject,
   `Message-ID` — and unwraps forwarded/replied threads to find the original sender
   behind `>` quoting.

5. Routes the cleaned body through an ordered chain of source-specific parsers
   (`route_and_parse`); the first parser that recognises the sender/subject/body wins:

   `VCB → UOB → Payoo → VNPay → Shopee → Grab → Timo → LearnedRegex → GenericOllama`

6. Parsed transactions go through the same dedup → rules → review pipeline as OCR imports
   (`app.services.ingest_service.commit_ingest_batch`), landing in the Review Inbox unless
   confidence is high enough to auto-approve.

### Parsers

| Parser | Source |
|--------|--------|
| `VCBParser` | Vietcombank |
| `UOBParser` | UOB card alerts |
| `PayooParser` | Payoo |
| `VNPayParser` | VNPay |
| `ShopeeParser` | Shopee orders/payments |
| `GrabParser` | Grab Bike/Car/Food/Express (extracts pickup→dropoff route from HTML) |
| `TimoParser` | Timo debit/credit |
| `LearnedRegexParser` | Regex patterns previously learned per sender domain (see below) |
| `GenericOllamaParser` | LLM fallback (vLLM) for unrecognised senders |

### Learned patterns

When the generic LLM fallback successfully extracts a transaction from a sender it hasn't
seen a dedicated parser for, it fires a second LLM call to derive regex patterns for that
sender's format and saves them to the `learned_patterns` **database table** (keyed by
sender domain) — they survive pod restarts and ride along with the regular DB backups. On
subsequent emails from the same domain, `LearnedRegexParser` applies those patterns first,
skipping the LLM call entirely.

Pattern lifecycle: every successful match bumps `success_count` and resets the
`failure_count` streak; after **5 consecutive misses** (sender changed their template) the
patterns are dropped so the LLM re-learns the new format on the next email.

### AI parser human approval gate

LLM-generated regex parsers (both from the email worker's `GenericOllamaParser` and the
OCR worker's AI fallback loop) require **manual approval** before they activate. A
security gate prevents AI-generated code from running without a human reviewing it first.
Approvals can be managed via **Import → Email Receipts** → Approved Parsers.

### Retry & failure handling

All retries are **database-driven** — they replay the stored raw copy, independent of the
IMAP mailbox state:

- Processing failures schedule a retry with exponential backoff (1 min, 2 min, 4 min, …)
  via `retry_after` / `retry_count`; after `MAX_EMAIL_RETRIES` the row is marked `failed`
  and its raw copy is kept for manual reprocessing.
- **LLM unavailable is not a failure**: when no dedicated parser matches and the vLLM
  fallback is unreachable (e.g. the GPU node is powered off), the email stays `pending`
  and is retried every `LLM_RETRY_MIN` minutes *without* consuming retry attempts — it is
  processed automatically once the model is back.
- Rows stuck in `pending` with no `retry_after` (worker crashed mid-processing) are
  reclaimed after `STUCK_TIMEOUT_MIN` and retried from the stored copy.
- A liveness file at `/tmp/worker_alive` is touched at least every ~60 s for the
  container health check; worker health (`last seen`, last connection error) and 7-day
  counters are shown in **Import → Email Receipts**.

---

## Configuration

Most settings can be configured either via environment variables or from **Settings →
Email Integration** in the app (DB-stored values take precedence and are re-read every
cycle; host/credential/folder changes trigger a clean reconnect).

| Variable | Default | Purpose |
|----------|---------|---------|
| `IMAP_HOST` | `imap.gmail.com` | IMAP server |
| `IMAP_USER` | — | Full Gmail address (**required**) |
| `IMAP_PASSWORD` | — | Gmail **App Password** — not the account password; requires 2FA (**required**) |
| `IMAP_FOLDER` | `INBOX` | Mailbox to watch |
| `IMAP_TIMEOUT` | `60` | Socket timeout (seconds) — a hung connection reconnects instead of stalling |
| `DATABASE_URL` | `postgresql://carange:***@localhost:5432/carange` | Same PostgreSQL database as the main app |
| `POLL_INTERVAL` | `300` | Seconds between polls when IDLE is unavailable |
| `STUCK_TIMEOUT_MIN` | `30` | Minutes before a crashed `pending` row is reclaimed |
| `MAX_EMAIL_RETRIES` | `3` | Retry attempts (exponential backoff) before permanent failure |
| `LLM_RETRY_MIN` | `30` | Minutes between retries while the LLM fallback is unreachable |
| `OLLAMA_URL` | — | vLLM endpoint for the generic fallback parser (unset = emails from unknown senders wait for it) |
| `OLLAMA_MODEL` | `Qwen3.6-35B-A3B` | Model name served by vLLM |

Set up a Gmail filter that forwards bank/payment notification emails to the watched mailbox
(or watches the inbox directly), and create an [App Password](https://myaccount.google.com/apppasswords)
for `IMAP_PASSWORD`.

---

## Running

Enabled by uncommenting the `email_worker` service in `docker-compose.yml` and setting
the `IMAP_*` variables — see the **Self-Hosting** section of the [main README](../README.md).

Locally:

```bash
cd carange_app/carange
DATABASE_URL=postgresql://carange:***@localhost:5432/carange \
  IMAP_USER=you@gmail.com IMAP_PASSWORD=<app-password> \
  python -m email_worker.worker
```

It shares the main app's database and `requirements.txt`; worker-specific dependencies
(`beautifulsoup4`, `lxml`, `imapclient`) live in `email_worker/requirements.txt`.
