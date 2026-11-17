.DEFAULT_GOAL := help
PY ?= .venv/bin/python
BIN := .venv/bin

.PHONY: help setup index dev serve demo test cov eval eval-guardrails eval-claude eval-architecture eval-retrieval e2e-smoke lint format typecheck check web-install web-dev web-build web-test up down clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-11s\033[0m %s\n", $$1, $$2}'

setup: ## Create the virtualenv and install everything (uses uv if available)
	@command -v uv >/dev/null && uv venv --quiet .venv && uv pip install --quiet --python $(PY) -e ".[dev]" \
		|| (python3 -m venv .venv && $(BIN)/pip install --quiet -e ".[dev]")
	@test -f .env || cp .env.example .env
	@command -v npm >/dev/null && (cd web && npm install --silent) || echo "npm not found: skipping the web UI"
	$(BIN)/pre-commit install --install-hooks >/dev/null 2>&1 || true
	@echo "Ready. Next: make dev"

index: ## Rebuild the runbook search index
	$(BIN)/incidentops runbooks index

dev: index ## Run the API with auto-reload on :8000 (run `make web-dev` in a second terminal for the UI)
	INCIDENTOPS_ENV=dev LOG_JSON=false $(BIN)/incidentops serve --reload

web-install: ## Install web UI dependencies
	cd web && npm install

web-dev: ## Run the web UI with hot reload on http://localhost:5173 (proxies to the API on :8000)
	cd web && npm run dev

web-build: ## Build the web UI into the Python package so the API serves it at /
	cd web && npm run build

web-test: ## Typecheck and test the web UI
	cd web && npm run typecheck && npm test

serve: ## Run API + built UI (production settings; requires INCIDENTOPS_API_KEY)
	$(BIN)/incidentops serve --host 0.0.0.0

demo: index ## Run a sample incident end to end in the terminal
	INCIDENTOPS_ENV=dev LOG_LEVEL=ERROR $(BIN)/incidentops incident --fixture tests/fixtures/incident_complete.json --approval approved

test: ## Run the test suite
	$(BIN)/pytest -q

cov: ## Run tests with coverage (fails under 85%)
	$(BIN)/pytest -q --cov

eval: ## Score core + adversarial/edge-case incidents with the offline rules provider (free)
	$(PY) -m evals.run_evals --provider rules

eval-guardrails: ## Measure the guardrails: attack block rate and false-positive rate (free, no model)
	$(PY) -m evals.guardrail_eval

eval-claude: ## Compare rules vs Claude on the labeled incidents (needs ANTHROPIC_API_KEY; costs money)
	$(PY) -m evals.run_evals --provider both

eval-architecture: ## Test whether parallel specialist agents beat the current graph
	$(PY) -m evals.compare_architectures

e2e-smoke: index demo ## End-to-end CLI smoke (demo fixture + timeline asserts)
	@INCIDENTOPS_ENV=dev LOG_LEVEL=ERROR $(BIN)/incidentops incident \
		--fixture tests/fixtures/incident_complete.json | $(PY) -c "import json,sys; s=json.load(sys.stdin); t=' '.join(s.get('timeline',[])); assert 'research' in t and 'assessed' in t, t"

eval-retrieval: ## Score runbook retrieval (FTS vs query rewrite; hybrid when [rag] is installed)
	$(PY) -m evals.run_retrieval_evals --modes fts,rewrite,hybrid

lint: ## Lint and check formatting
	$(BIN)/ruff check .
	$(BIN)/ruff format --check .

format: ## Auto-format and fix lint issues
	$(BIN)/ruff check --fix .
	$(BIN)/ruff format .

typecheck: ## Strict type checking
	$(BIN)/mypy

check: lint typecheck cov eval eval-guardrails eval-retrieval web-test ## Everything CI runs

up: ## Start API + n8n with Docker Compose
	docker compose up --build

down: ## Stop Docker Compose
	docker compose down

clean: ## Remove local state, caches and the built UI
	rm -rf .data src/incidentops/static web/dist .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov
