.DEFAULT_GOAL := help
PY ?= .venv/bin/python
BIN := .venv/bin
# Docker Compose v2 plugin if present, else the standalone binary.
COMPOSE ?= $(shell docker compose version >/dev/null 2>&1 && echo "docker compose" || echo docker-compose)
# Postgres and Redis for the tests (see compose.test.yaml). TESTS_STRICT makes a missing service or
# optional extra fail the run instead of skipping, so "all passed" really means everything ran.
TEST_ENV := TESTS_STRICT=1 \
	TEST_DATABASE_URL=postgresql://postgres:test@localhost:55432/postgres \
	TEST_REDIS_URL=redis://localhost:56379/0

.PHONY: help setup index dev serve demo test test-fast test-services test-services-down cov eval eval-guardrails eval-claude eval-architecture eval-retrieval e2e-smoke badges badges-check n8n-build n8n-check e2e-n8n loadtest lint format typecheck check web-install web-dev web-build web-test up down clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-19s\033[0m %s\n", $$1, $$2}'

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

test-services: ## Start throwaway Postgres + Redis for the tests (needs Docker)
	@$(COMPOSE) -f compose.test.yaml up -d --wait

test-services-down: ## Stop and wipe the test Postgres + Redis
	@$(COMPOSE) -f compose.test.yaml down -v

test: test-services ## Run the whole test suite, Postgres and Redis included (needs Docker)
	$(TEST_ENV) $(BIN)/pytest -q

test-fast: ## Run the suite without Docker; the Postgres and Redis tests are skipped
	$(BIN)/pytest -q

cov: test-services ## Run all tests with coverage (fails under 85%)
	$(TEST_ENV) $(BIN)/pytest -q --cov

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

eval-retrieval: test-services ## Score runbook retrieval: SQLite FTS/rewrite/hybrid and the Postgres store (needs Docker)
	$(TEST_ENV) $(PY) -m evals.run_retrieval_evals --modes fts,rewrite,hybrid,pg-fts,pgvector

loadtest: ## Measure latency at 1, 2 and 4 API replicas and write evals/loadtest_report.md (needs Docker; ~5 min)
	$(PY) scripts/loadtest.py matrix --replicas 1,2,4 --users 16,64 --seconds 30

badges: ## Rewrite the README badge files from the eval reports (run the evals first)
	$(PY) scripts/update_badges.py

badges-check: ## Fail if the committed badge files are out of date with the eval reports
	$(PY) scripts/update_badges.py --check

n8n-build: ## Copy n8n/src/*.js into n8n/incident-ops.json
	$(PY) scripts/build_n8n.py

n8n-check: ## Fail if the workflow JSON is out of date with n8n/src
	$(PY) scripts/build_n8n.py --check

e2e-n8n: ## Run the workflow in real n8n containers (needs docker, Redis from `make test-services`, and the API on :8012)
	$(PY) scripts/e2e_n8n.py

lint: ## Lint and check formatting
	$(BIN)/ruff check .
	$(BIN)/ruff format --check .

format: ## Auto-format and fix lint issues
	$(BIN)/ruff check --fix .
	$(BIN)/ruff format .

typecheck: ## Strict type checking
	$(BIN)/mypy

check: lint typecheck cov eval eval-guardrails eval-retrieval badges-check n8n-check web-test ## Everything CI runs

up: ## Start API + n8n with Docker Compose
	docker compose up --build

down: ## Stop Docker Compose
	docker compose down

clean: ## Remove local state, caches and the built UI
	rm -rf .data src/incidentops/static web/dist .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov
