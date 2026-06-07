# Email Worker

Polls a Gmail IMAP inbox for forwarded bank/payment notification emails, parses them into
transactions, and feeds them into Carange's review pipeline. Runs as a separate container
(`ghcr.io/thevivotran/carange-email-worker`) alongside the main app.

---

## How it works

1. Logs into the configured IMAP mailbox every `POLL_INTERVAL` seconds and fetches `UNSEEN`
   messages.
2. Extracts MIME parts (`email_parser.py`) — plaintext, HTML, sender, subject, `Message-ID` —
   and unwraps forwarded/replied threads to find the original sender behind `>` quoting.
3. Routes the cleaned body through an ordered chain of source-specific parsers
   (`route_and_parse`); the first parser that recognises the sender/subject/body wins:

   `VCB → UOB → Payoo → VNPay → Shopee → Grab → Timo → LearnedRegex → GenericOllama`

4. Parsed transactions go through the same dedup → rules → review pipeline as OCR imports
   (`app.services.ingest_service.commit_ingest_batch`), landing in the Review Inbox unless
   confidence is high enough to auto-approve.
5. Marks the message `\Seen` in IMAP once processed (success or permanent failure) so it
   isn't picked up again.

Every poll is recorded in `EmailIngestLog` (keyed by `Message-ID`) — this is what drives
retries, dedup, and the Settings → Email Ingestion status panel.

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
| `GenericOllamaParser` | LLM fallback (Ollama) for unrecognised senders |

### Learned patterns

When the generic LLM fallback successfully extracts a transaction from a sender it hasn't
seen a dedicated parser for, it can save the regex patterns it derived to
`learned_patterns.json` (keyed by sender domain, via `learned_patterns.py`). On subsequent
emails from the same domain, `LearnedRegexParser` tries those patterns first — skipping the
LLM call entirely once a domain is "learned." Each successful match increments a
`success_count` used to gauge pattern reliability.

### Retry & failure handling

- Failures during processing leave the message `UNSEEN` and schedule a retry with exponential
  backoff (1 min, 2 min, 4 min, ... up to `MAX_EMAIL_RETRIES`), tracked via `retry_after` /
  `retry_count` on the log row.
- After the max retry count is exceeded, the row is marked `failed` and the message is marked
  `\Seen` so it stops blocking the poll loop.
- `EmailIngestLog` rows stuck in `pending` with no `retry_after` (from a crashed run) are
  reclaimed on the next poll so the same `UNSEEN` email can be reprocessed.
- A liveness file at `/tmp/worker_alive` is touched on every loop iteration for container
  health checks.

---

## Configuration

Most settings can be configured either via environment variables or from **Settings → Email
Integration** in the app (DB-stored values take precedence; `_load_config()` re-reads them
before every poll).

| Variable | Default | Purpose |
|----------|---------|---------|
| `IMAP_HOST` | `imap.gmail.com` | IMAP server |
| `IMAP_USER` | — | Full Gmail address (**required**) |
| `IMAP_PASSWORD` | — | Gmail **App Password** — not the account password; requires 2FA (**required**) |
| `IMAP_FOLDER` | `INBOX` | Mailbox to watch |
| `DATABASE_URL` | `sqlite:///./carange.db` | Same database as the main app |
| `POLL_INTERVAL` | `300` | Seconds between polls |
| `STUCK_TIMEOUT_MIN` | `30` | Minutes before a crashed `pending` log row is reclaimed |
| `MAX_EMAIL_RETRIES` | `3` | Retry attempts (exponential backoff) before permanent failure |

Set up a Gmail filter that forwards bank/payment notification emails to the watched mailbox
(or watches the inbox directly), and create an [App Password](https://myaccount.google.com/apppasswords)
for `IMAP_PASSWORD`.

---

## Running

Enabled by uncommenting the `email_worker` service in `docker-compose.yml` /
`docker-compose.pg.yml` and setting the `IMAP_*` variables — see the **Self-Hosting** section
of the [main README](../README.md).

Locally:

```bash
cd carange_app/carange
IMAP_USER=you@gmail.com IMAP_PASSWORD=<app-password> python -m email_worker.worker
```

It shares the main app's database and `requirements.txt`; worker-specific dependencies
(`beautifulsoup4`, `lxml`) live in `email_worker/requirements.txt`.
