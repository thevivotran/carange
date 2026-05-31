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

# ── Pre-push (all CI checks except Docker build) ──────────────────────────────

.PHONY: pre-push
pre-push: lint audit test
	@echo ""
	@echo "All checks passed — safe to push."

# ── Dev server ────────────────────────────────────────────────────────────────

.PHONY: run
run:
	$(PYTHON) main.py
