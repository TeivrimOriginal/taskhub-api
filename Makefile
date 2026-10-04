# =====================================================================================
# Every command a developer needs. `make help` lists them.
# Docker is the default path; the `local-*` targets run against a workstation database.
# =====================================================================================

.DEFAULT_GOAL := help
SHELL := /usr/bin/env bash
COMPOSE := docker compose
PY := python
UVICORN := uvicorn app.main:app --reload

.PHONY: help
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# ------------------------------------------------------------------ lifecycle
.PHONY: up
up: ## Build and start the whole stack in the background
	$(COMPOSE) up --build -d
	@echo "Swagger:  http://localhost:8000/docs"
	@echo "Mailpit:  http://localhost:8025"
	@echo "Nginx:    http://localhost"

.PHONY: down
down: ## Stop the stack, keeping volumes
	$(COMPOSE) down

.PHONY: restart
restart: down up ## Recreate the stack

.PHONY: nuke
nuke: ## Stop the stack and delete its volumes (data loss is intentional)
	$(COMPOSE) down -v --remove-orphans

.PHONY: ps
ps: ## Show container status
	$(COMPOSE) ps

.PHONY: logs
logs: ## Tail the API logs
	$(COMPOSE) logs -f api

.PHONY: logs-worker
logs-worker: ## Tail the Celery worker logs
	$(COMPOSE) logs -f worker

.PHONY: shell
shell: ## psql inside the database container
	$(COMPOSE) exec postgres psql -U taskhub -d taskhub

.PHONY: redis-cli
redis-cli: ## Redis CLI inside the cache container
	$(COMPOSE) exec redis redis-cli

.PHONY: bash
bash: ## Shell inside the API container
	$(COMPOSE) exec api /bin/bash

# ------------------------------------------------------------------- database
.PHONY: migrate
migrate: ## Apply every pending migration
	alembic upgrade head

.PHONY: makemigration
makemigration: ## Autogenerate a migration from the models (m="message")
	alembic revision --autogenerate -m "$(or $(m),auto)"

.PHONY: migration-check
migration-check: ## Fail if the models drifted away from the migrations
	alembic check

.PHONY: downgrade
downgrade: ## Roll back one migration (rev=0002)
	alembic downgrade $(or $(rev),-1)

.PHONY: migration-verify
migration-verify: ## Upgrade/downgrade the database and diff it against the models (DESTRUCTIVE)
	$(PY) scripts/verify_migrations.py

.PHONY: seed
seed: ## Create the admin account and a demo data set
	$(PY) -m app.cli create-admin
	$(PY) -m app.cli seed-demo

# ----------------------------------------------------------------------- tests
.PHONY: test
test: ## Run the whole suite (reports coverage, enforces no gate)
	$(PY) -m pytest

.PHONY: test-unit
test-unit: ## Fast tests that need no database
	$(PY) -m pytest -m unit -q --no-cov

.PHONY: test-integration
test-integration: ## Tests that touch PostgreSQL
	$(PY) -m pytest -m integration -q --no-cov

# The coverage number is only trustworthy under PEP 669 `sys.monitoring`; the C tracer
# loses the statement after every `await` in this code base. `sysmon` cannot measure
# branches before Python 3.14, so the gate runs there and nowhere else. See
# `[tool.coverage.run]` in pyproject.toml for the measurement.
.PHONY: gate
gate: ## Run the suite and fail below the 85% coverage gate (needs Python >= 3.14)
	COVERAGE_CORE=sysmon $(PY) -m pytest --cov-fail-under=85 --cov-report=term-missing

.PHONY: coverage
coverage: ## Open the HTML coverage report
	$(PY) -m coverage html && echo "report: htmlcov/index.html"

.PHONY: smoke
smoke: ## Drive a *running* server end to end (needs `make up` or `make run`)
	$(PY) scripts/smoke.py --base-url http://127.0.0.1:8000

# ------------------------------------------------------------------ local run
.PHONY: run
run: ## Run the API locally with autoreload
	$(UVICORN) --host 127.0.0.1 --port 8000

.PHONY: run-worker
run-worker: ## Run a Celery worker locally
	celery -A app.core.celery_app:celery_app worker --loglevel=info

.PHONY: run-beat
run-beat: ## Run Celery beat locally
	celery -A app.core.celery_app:celery_app beat --loglevel=info

# --------------------------------------------------------------------- quality
.PHONY: lint
lint: ## ruff check + format check
	ruff check .
	ruff format --check .

.PHONY: format
format: ## Apply ruff fixes and reformat
	ruff check --fix .
	ruff format .

.PHONY: typecheck
typecheck: ## mypy in strict mode
	mypy

.PHONY: precommit
precommit: ## Run every pre-commit hook over the repository
	pre-commit run --all-files

.PHONY: precommit-install
precommit-install: ## Install the git hooks
	pre-commit install

# ----------------------------------------------------------------- housekeeping
.PHONY: install
install: ## Install the project with its development extras
	$(PY) -m pip install -e ".[dev]"

.PHONY: clean
clean: ## Remove caches and build artefacts
	rm -rf .pytest_cache .ruff_cache .mypy_cache htmlcov .coverage coverage.xml build dist *.egg-info
	find . -type d -name __pycache__ -prune -exec rm -rf {} +

.PHONY: verify
verify: lint typecheck gate migration-check ## Everything CI checks, in one command
	@echo "OK: lint, types, tests, the coverage gate and migration state all pass"