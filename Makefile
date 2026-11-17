.DEFAULT_GOAL := help
PY ?= .venv/bin/python
BIN := .venv/bin

.PHONY: help setup index dev serve demo test cov eval eval-claude lint format typecheck check up down clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-11s\033[0m %s\n", $$1, $$2}'

setup: ## Create the virtualenv and install everything (uses uv if available)
	@command -v uv >/dev/null && uv venv --quiet .venv && uv pip install --quiet --python $(PY) -e ".[dev]" \
		|| (python3 -m venv .venv && $(BIN)/pip install --quiet -e ".[dev]")
	@test -f .env || cp .env.example .env
	$(BIN)/pre-commit install --install-hooks >/dev/null 2>&1 || true
	@echo "Ready. Next: make dev"

index: ## Rebuild the runbook search index
	$(BIN)/incidentops runbooks index

dev: index ## Run API + dashboard with auto-reload at http://localhost:8000
	INCIDENTOPS_ENV=dev LOG_JSON=false $(BIN)/incidentops serve --reload

serve: ## Run API + dashboard (production settings; requires INCIDENTOPS_API_KEY)
	$(BIN)/incidentops serve --host 0.0.0.0

demo: index ## Run a sample incident end to end in the terminal
	INCIDENTOPS_ENV=dev LOG_LEVEL=ERROR $(BIN)/incidentops incident --fixture tests/fixtures/incident_complete.json --approval approved

test: ## Run the test suite
	$(BIN)/pytest -q

cov: ## Run tests with coverage (fails under 85%)
	$(BIN)/pytest -q --cov

eval: ## Score the labeled incidents with the offline rules provider (free)
	$(PY) -m evals.run_evals --provider rules

eval-claude: ## Compare rules vs Claude on the labeled incidents (needs ANTHROPIC_API_KEY; costs money)
	$(PY) -m evals.run_evals --provider both

lint: ## Lint and check formatting
	$(BIN)/ruff check .
	$(BIN)/ruff format --check .

format: ## Auto-format and fix lint issues
	$(BIN)/ruff check --fix .
	$(BIN)/ruff format .

typecheck: ## Strict type checking
	$(BIN)/mypy

check: lint typecheck cov eval ## Everything CI runs

up: ## Start API + n8n with Docker Compose
	docker compose up --build

down: ## Stop Docker Compose
	docker compose down

clean: ## Remove local state and caches
	rm -rf .data .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov
