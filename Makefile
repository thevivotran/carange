PYTHON := .venv/bin/python
PYTEST := .venv/bin/pytest
RUFF   := $(shell command -v ruff 2>/dev/null || echo .venv/bin/ruff)

# ── Default ───────────────────────────────────────────────────────────────────

.PHONY: all
all: lint test

# ── Lint (mirrors CI stage 1) ─────────────────────────────────────────────────

.PHONY: lint
lint:
	@echo "── ruff: errors & style ──────────────────────────────────────────"
	$(RUFF) check .
	@echo "── ruff: formatting ──────────────────────────────────────────────"
	$(RUFF) format --check .
	@echo "── ui-lint: design token check ───────────────────────────────────"
	$(PYTEST) tests/test_ui_lint.py -q

.PHONY: fmt
fmt:
	$(RUFF) format .
	$(RUFF) check . --fix

# ── Test (mirrors CI stage 2) ─────────────────────────────────────────────────

.PHONY: test
test: migrate-fresh
	@echo "── smoke: app import ─────────────────────────────────────────────"
	DATABASE_URL=$(PG_TEST_URL) $(PYTHON) -c "from main import app; print('App startup OK')"
	@echo "── schema sync: ORM vs migrations ───────────────────────────────"
	DATABASE_URL=$(PG_TEST_URL) TEST_DATABASE_URL=$(PG_TEST_URL) $(PYTEST) tests/test_schema_sync.py -v
	@echo "── pytest with coverage ────────────────────────────────────────────"
	DATABASE_URL=$(PG_TEST_URL) TEST_DATABASE_URL=$(PG_TEST_URL) $(PYTEST) --cov=app --cov-report=term-missing --cov-fail-under=94

.PHONY: test-fast
test-fast:
	DATABASE_URL=$(PG_TEST_URL) TEST_DATABASE_URL=$(PG_TEST_URL) $(PYTEST) -q

# ── Audit ─────────────────────────────────────────────────────────────────────

.PHONY: audit
audit:
	@echo "── pip-audit ─────────────────────────────────────────────────────"
	$(PYTHON) -m pip_audit -r requirements.txt

# ── Local PostgreSQL (podman) ─────────────────────────────────────────────────
# Force Podman to use the canonical XDG data dir so the storage DB is always
# at ~/.local/share/containers regardless of whether we're running inside a
# snap-isolated editor (which overrides XDG_DATA_HOME to its own prefix).
export XDG_DATA_HOME := $(HOME)/.local/share

PG_CONTAINER := carange-pg
PG_IMAGE     := docker.io/postgres:16-alpine
PG_URL       ?= postgresql://carange:carange@localhost:5432/carange
PG_TEST_URL  := postgresql://carange:carange@localhost:5432/carange_test

.PHONY: db-up
db-up:
	@if ! podman ps --format '{{.Names}}' 2>/dev/null | grep -q '^$(PG_CONTAINER)$$'; then \
	    podman run -d --name $(PG_CONTAINER) \
	        -e POSTGRES_USER=carange \
	        -e POSTGRES_PASSWORD=carange \
	        -e POSTGRES_DB=carange \
	        -p 5432:5432 \
	        $(PG_IMAGE); \
	fi
	@echo "Waiting for postgres to be ready..."
	@until podman exec $(PG_CONTAINER) pg_isready -U carange > /dev/null 2>&1; do sleep 1; done
	@echo "PostgreSQL is ready."

.PHONY: db-down
db-down:
	podman rm -f $(PG_CONTAINER) 2>/dev/null || true

.PHONY: migrate
migrate:
	DATABASE_URL=$(PG_URL) .venv/bin/alembic upgrade head

.PHONY: migrate-fresh
migrate-fresh: db-up
	podman exec $(PG_CONTAINER) psql -U carange -c "DROP DATABASE IF EXISTS carange_test;" || true
	podman exec $(PG_CONTAINER) psql -U carange -c "CREATE DATABASE carange_test;"
	DATABASE_URL=$(PG_TEST_URL) .venv/bin/alembic upgrade head
	@echo "Fresh migration against carange_test OK."



# ── Pre-push (all CI checks except Docker build) ──────────────────────────────

.PHONY: pre-push
pre-push: lint audit test
	@echo ""
	@echo "All checks passed — safe to push."

# ── Dev server ────────────────────────────────────────────────────────────────

.PHONY: run
run:
	$(PYTHON) main.py
