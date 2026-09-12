# =============================================================================
# Every task you need, in one place. Run `make` for the list.
#
# Windows: run these from WSL2, Git Bash, or use the `docker compose` commands
# directly — each target's body is a plain shell command you can copy.
# =============================================================================
.DEFAULT_GOAL := help
SHELL := /bin/bash
COMPOSE := docker compose

# Colours for the help output. Degrade harmlessly on terminals without them.
BOLD := \033[1m
DIM  := \033[2m
CYAN := \033[36m
OFF  := \033[0m

.PHONY: help
help: ## Show this help
	@echo ""
	@printf "$(BOLD)e-comm template$(OFF) $(DIM)— full-stack microservice scaffold$(OFF)\n"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| sort \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  $(CYAN)%-20s$(OFF) %s\n", $$1, $$2}'
	@echo ""

# -----------------------------------------------------------------------------
# Setup
# -----------------------------------------------------------------------------
.PHONY: setup
setup: ## First-time setup: create .env, generate secrets, build images
	@test -f .env || (cp .env.example .env && echo "Created .env from .env.example")
	@$(MAKE) --no-print-directory secrets
	@echo ""
	@echo "Next: add your Stripe test keys to .env, then run 'make up'."

.PHONY: secrets
secrets: ## Fill placeholder secrets in .env with strong random values
	@python3 scripts/generate_secrets.py

# -----------------------------------------------------------------------------
# Running the stack
# -----------------------------------------------------------------------------
.PHONY: up
up: ## Build and start every service in the background
	$(COMPOSE) up -d --build
	@echo ""
	@echo "  Storefront   http://localhost:3000"
	@echo "  Admin        http://localhost:3000/admin"
	@echo "  API gateway  http://localhost:8080"
	@echo "  API docs     http://localhost:8080/docs"
	@echo "  Mail catcher http://localhost:8025"
	@echo ""
	@echo "  Follow logs with 'make logs'. Check health with 'make health'."

.PHONY: down
down: ## Stop every service (data is preserved)
	$(COMPOSE) down

.PHONY: clean
clean: ## Stop everything and DELETE all data volumes
	@printf "This deletes the database and all local data. Type 'yes' to confirm: " && read ans && [ "$$ans" = "yes" ]
	$(COMPOSE) down -v --remove-orphans

.PHONY: restart
restart: ## Restart a single service, e.g. make restart SVC=orders
	$(COMPOSE) restart $(SVC)

.PHONY: rebuild
rebuild: ## Rebuild and restart one service, e.g. make rebuild SVC=orders
	$(COMPOSE) up -d --build $(SVC)

.PHONY: logs
logs: ## Tail logs (all services, or one via SVC=orders)
	$(COMPOSE) logs -f --tail=100 $(SVC)

.PHONY: ps
ps: ## Show the status of every container
	$(COMPOSE) ps

.PHONY: health
health: ## Query every service's health endpoint
	@bash scripts/health_check.sh

.PHONY: shell
shell: ## Open a shell in a service container, e.g. make shell SVC=orders
	$(COMPOSE) exec $(SVC) /bin/bash

.PHONY: psql
psql: ## Open psql as the database owner
	$(COMPOSE) exec -e PGPASSWORD="$$(grep '^POSTGRES_PASSWORD=' .env | cut -d= -f2)" postgres psql -U $$(grep '^POSTGRES_USER=' .env | cut -d= -f2) -d $$(grep '^POSTGRES_DB=' .env | cut -d= -f2)

# -----------------------------------------------------------------------------
# Development (runs on your machine, not in Docker)
# -----------------------------------------------------------------------------
.PHONY: install
install: ## Install all Python dependencies into a local .venv
	uv sync --all-packages

.PHONY: lock
lock: ## Re-resolve uv.lock after changing a dependency
	uv lock

.PHONY: fmt
fmt: ## Auto-format and auto-fix Python
	uv run ruff format .
	uv run ruff check --fix .

.PHONY: lint
lint: ## Lint Python and TypeScript
	uv run ruff format --check .
	uv run ruff check .
	cd web && npm run lint

.PHONY: typecheck
typecheck: ## Type-check Python and TypeScript
	uv run mypy packages services
	cd web && npm run typecheck

.PHONY: test
test: ## Run the Python test suite
	uv run pytest

.PHONY: test-cov
test-cov: ## Run tests with a coverage report
	uv run pytest --cov=packages --cov=services --cov-report=term-missing

.PHONY: check
check: lint typecheck test ## Everything CI runs. Run before you push.

# -----------------------------------------------------------------------------
# Database
# -----------------------------------------------------------------------------
.PHONY: migrate
migrate: ## Apply migrations for one service, e.g. make migrate SVC=orders
	$(COMPOSE) exec $(SVC) alembic upgrade head

.PHONY: migration
migration: ## Autogenerate a migration, e.g. make migration SVC=orders M="add sku"
	$(COMPOSE) exec $(SVC) alembic revision --autogenerate -m "$(M)"

.PHONY: seed
seed: ## Load demo products, users and orders for local development
	$(COMPOSE) exec catalog python -m ecom_catalog.seed
	$(COMPOSE) exec orders python -m ecom_orders.seed

# -----------------------------------------------------------------------------
# Analytics (dbt)
# -----------------------------------------------------------------------------
.PHONY: dbt-run
dbt-run: ## Build the dbt models into Postgres and DuckDB
	$(COMPOSE) run --rm dbt run

.PHONY: dbt-test
dbt-test: ## Run dbt data-quality tests
	$(COMPOSE) run --rm dbt test

.PHONY: dbt-build
dbt-build: ## dbt run + test in dependency order
	$(COMPOSE) run --rm dbt build

.PHONY: dbt-docs
dbt-docs: ## Generate and serve the dbt lineage docs on :8081
	$(COMPOSE) run --rm --service-ports dbt docs serve --host 0.0.0.0 --port 8081

# -----------------------------------------------------------------------------
# Frontend
# -----------------------------------------------------------------------------
.PHONY: web-install
web-install: ## Install frontend dependencies
	cd web && npm install

.PHONY: web-dev
web-dev: ## Run the Next.js dev server on your machine with hot reload
	cd web && npm run dev
