# Contributing to Carange

Thanks for your interest in Carange! This is primarily a personal/family project, but
issues, bug reports, and pull requests are welcome.

## Getting started

```bash
git clone git@github.com:thevivotran/carange.git
cd carange
uv sync                              # or: pip install -r requirements.txt
python main.py                       # → http://localhost:6868
```

See the [README](README.md) for environment variables and the full local setup.

**Note:** Carange requires PostgreSQL. Start a local instance with `make db-up`
(uses Podman) or point `DATABASE_URL` at any PostgreSQL 16+ server.

## Development workflow

1. Create a feature branch off `main`: `git checkout -b feat/your-feature`
2. Make your changes, following the project conventions (see Code style below).
3. Run the full check suite before pushing:

   ```bash
   make pre-push   # lint + dependency audit + tests with ≥95% coverage
   ```

4. Commit using [Conventional Commits](https://www.conventionalcommits.org/)
   (`feat:`, `fix:`, `chore:`, `docs:`, `refactor:`, `test:`, etc.), e.g.
   `feat(notify): add quarterly recurrence option`.
5. Open a pull request against `main`.

**Git remote note:** This repo uses the SSH alias `git@thevivotran:thevivotran/carange.git`
(configured in `~/.ssh/config`), not `git@github.com:`. If push fails, check your
`~/.ssh/config` has the right host alias. There is no `gh` CLI — open PRs via a compare
link: `https://github.com/thevivotran/carange/compare/main...<branch>?expand=1`

## Code style

- **Python** — linting and formatting via `ruff` (`make lint` / `ruff format`).
- **Architecture** — routes go in `app/routers/`, business logic in `app/services/`,
  models in `app/models/database.py` (SQLAlchemy) and `app/models/schemas.py` (Pydantic).
- **Database schema** — changes require a matching Alembic migration. CI runs
  `test_schema_sync.py` to keep the ORM and migration chain in sync.
- **Templates** — Jinja2 + HTMX + Tailwind. Never use `innerHTML` with dynamic content
  (a git hook blocks it). Use `createElement` / `textContent` / `appendChild` exclusively.
- **Notifications** — Telegram message formatting helpers live in `app/notify/telegram.py`.
  The notify worker (`notify_worker/worker.py`) processes the `notification_events` queue
  via PostgreSQL LISTEN/NOTIFY.

## Tests

- `make test` — full suite with coverage against a PostgreSQL test database
  (auto-creates `carange_test` DB). Requires `make db-up` for the PostgreSQL container.
- `make test-fast` — quick run without coverage.

New features and bug fixes should include tests. CI enforces a minimum of 95% coverage.

**~1,080 tests** across 42+ modules covering:
- Route handlers, services, models, fragments
- OCR and email worker logic
- Telegram notification formatting
- Budget, forecast, fiscal period, Pulse
- Schema sync (ORM vs Alembic migrations)
- UI lint (design token enforcement)

## Using Hermes Agent (recommended)

This project has a companion **Carange skill** loaded by Hermes Agent:

```
hermes skills list         # should show "carange-app" under software-development
hermes -s carange-app     # start a session with the skill pre-loaded
```

The skill contains the project guide, gotchas, and architecture reference. Other
related skills: `homelab` (k3s deployment), `projects-overview` (all projects).

## Releases

A `release-please` bot maintains a standing "Release PR" against `main`, computing the
next version from Conventional Commit types since the last release (`fix:` → patch,
`feat:` → minor, `feat!:`/`BREAKING CHANGE:` → major). Merging that PR updates
`CHANGELOG.md` and `pyproject.toml`, and creates a `vX.Y.Z` tag + GitHub Release. No
manual versioning or tagging is needed.

## Reporting issues

Please include:
- Steps to reproduce
- Expected vs. actual behavior
- Environment (Docker/local, database backend, browser if UI-related)
