# Contributing to Carange

Thanks for your interest in Carange! This is primarily a personal/family project, but
issues, bug reports, and pull requests are welcome.

## Getting started

```bash
git clone git@github.com:thevivotran/carange.git
cd carange
pip install -r requirements.txt   # or: uv sync
python main.py                     # → http://localhost:6868
```

See the [README](README.md) for environment variables and the full local setup.

## Development workflow

1. Create a feature branch off `main`: `git checkout -b feat/your-feature`
2. Make your changes, following the conventions in [`AGENTS.md`](AGENTS.md) /
   [`CLAUDE.md`](CLAUDE.md) if present.
3. Run the full check suite before pushing:

   ```bash
   make pre-push   # lint + dependency audit + tests with ≥95% coverage
   ```

4. Commit using [Conventional Commits](https://www.conventionalcommits.org/)
   (`feat:`, `fix:`, `chore:`, `docs:`, `refactor:`, `test:`), e.g.
   `feat(notify): add quarterly recurrence option`.
5. Open a pull request against `main`.

## Code style

- Linting and formatting via `ruff` (`make lint` / `ruff format`).
- Routes go in `app/routers/`, business logic in `app/services/`, models in
  `app/models/database.py` (SQLAlchemy) and `app/models/schemas.py` (Pydantic).
- Database schema changes require a matching Alembic migration — see
  `tests/test_schema_sync.py`, which CI runs to keep the ORM and migration chain in sync.
- Templates use Jinja2 + HTMX + Tailwind. Avoid `innerHTML` with dynamic content; use
  `createElement` / `textContent` / `appendChild`.

## Tests

- `make test` — full suite with coverage (requires PostgreSQL via `make db-up`)
- `make test-fast` — quick run without coverage, using SQLite

New features and bug fixes should include tests. CI enforces a minimum of 95% coverage.

## Reporting issues

Please include:
- Steps to reproduce
- Expected vs. actual behavior
- Environment (Docker/local, database backend, browser if UI-related)
