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
	$(PYTHON) ui_lint.py

.PHONY: fmt
fmt:
	$(RUFF) format .
	$(RUFF) check . --fix

# ── Test (mirrors CI stage 2) ─────────────────────────────────────────────────

.PHONY: test
test:
	@echo "── smoke: app import ─────────────────────────────────────────────"
	$(PYTHON) -c "from main import app; print('App startup OK')"
	@echo "── schema sync: ORM vs migrations ───────────────────────────────"
	$(PYTEST) tests/test_schema_sync.py -v
	@echo "── pytest with coverage ──────────────────────────────────────────"
	$(PYTEST) --cov=app --cov-report=term-missing --cov-fail-under=95

.PHONY: test-fast
test-fast:
	$(PYTEST) -q

# ── Audit ─────────────────────────────────────────────────────────────────────

.PHONY: audit
audit:
	@echo "── pip-audit ─────────────────────────────────────────────────────"
	$(PYTHON) -m pip_audit -r requirements.txt

# ── Local PostgreSQL (docker-compose) ─────────────────────────────────────────

PG_URL ?= postgresql://carange:carange@localhost:5432/carange

.PHONY: db-up
db-up:
	docker compose up -d postgres
	@echo "Waiting for postgres to be ready..."
	@until docker compose exec postgres pg_isready -U carange > /dev/null 2>&1; do sleep 1; done
	@echo "PostgreSQL is ready."

.PHONY: db-down
db-down:
	docker compose down

.PHONY: migrate
migrate:
	DATABASE_URL=$(PG_URL) .venv/bin/alembic upgrade head

.PHONY: migrate-fresh
migrate-fresh:
	docker compose exec postgres psql -U carange -c "DROP DATABASE IF EXISTS carange_test; CREATE DATABASE carange_test;"
	DATABASE_URL=postgresql://carange:carange@localhost:5432/carange_test .venv/bin/alembic upgrade head
	@echo "Fresh migration against carange_test OK."

.PHONY: test-pg
test-pg: db-up migrate-fresh
	@echo "── migration smoke test against real PostgreSQL ──────────────────"
	DATABASE_URL=postgresql://carange:carange@localhost:5432/carange_test \
	    $(PYTEST) tests/test_schema_sync.py -v
	@echo "── PostgreSQL migration test passed ──────────────────────────────"

# ── Pre-push (all CI checks except Docker build) ──────────────────────────────

.PHONY: pre-push
pre-push: lint audit test test-pg
	@echo ""
	@echo "All checks passed — safe to push."

# ── Dev server ────────────────────────────────────────────────────────────────

.PHONY: run
run:
	$(PYTHON) main.py
