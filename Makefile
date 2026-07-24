# Agency Analytics Kit - Makefile
# uv for deps, Docker Compose for infra

SHELL := /bin/bash
.ONESHELL:
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

.PHONY: help install lock lint format check typecheck test quality
.PHONY: clean pristine
.PHONY: setup-networks setup-env pipeline
.PHONY: docker-build docker-build-pipeline
.PHONY: docker-up docker-up-all docker-down docker-logs
.PHONY: docker-db docker-pipeline docker-metabase

# === HELP ==================================================================

help:           ## Show this help
	@printf '\033[1mUsage:\033[0m  make \033[36m<target>\033[0m\n\n'
	@awk 'BEGIN {FS = ":.*##"; section=""} \
	  /^# ===/ {section=$$0; gsub(/^# === ?| ?=+$$/,"",section); next} \
	  /^[a-zA-Z_-]+:.*##/ { \
	    if (section != "") {printf "\n  \033[1m%s\033[0m\n", section; section=""} \
	    cmd = $$1; sub(/:.*/,"",cmd); \
	    desc = $$NF; \
	    printf "    \033[36m%-22s\033[0m %s\n", cmd, desc \
	  }' $(MAKEFILE_LIST)

# === DEPENDENCIES ==========================================================

install:        ## Install deps (uv sync)
	uv sync

lock:           ## Lock deps after adding a package
	uv lock && uv sync

# === QUALITY GATE ==========================================================

lint:           ## Lint with ruff
	uv run ruff check src/ tests/

format:         ## Format with ruff
	uv run ruff format src/ tests/

check:          ## Check format (read-only)
	uv run ruff format --check src/ tests/

typecheck:      ## Type-check with mypy
	uv run mypy src/

test:           ## Run test suite
	uv run pytest tests/ -v

quality:        ## Full gate: lint -> typecheck -> test
	$(MAKE) lint
	$(MAKE) typecheck
	$(MAKE) test

# === CLEANUP ===============================================================

clean:          ## Remove Python artifacts
	rm -rf .pytest_cache .mypy_cache .ruff_cache __pycache__
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name '*.pyc' -delete

pristine: clean ## Remove everything + .venv
	rm -rf .venv

# === LOCAL SETUP ===========================================================

setup-networks: ## Create Docker networks
	./scripts/setup-networks.sh

setup-env:      ## Copy global .env.example -> .env
	@if [ ! -f .env ]; then cp .env.example .env; echo "Created .env"; else echo ".env already exists."; fi

pipeline:       ## Run pipeline orchestration
	./scripts/pipeline.sh

# === DOCKER - BUILD ========================================================

docker-build:           ## Build all images
	docker build -t agency-pipeline:latest .

docker-build-nocache:   ## Cold build (no layer cache)
	docker build --no-cache -t agency-pipeline:latest .

# === DOCKER - STACKS =======================================================

docker-up:      ## Start Postgres + Pipeline
	$(MAKE) docker-db
	sleep 3
	$(MAKE) docker-pipeline

docker-up-all:  ## Start Postgres + Pipeline + Metabase
	$(MAKE) docker-db
	sleep 3
	$(MAKE) docker-pipeline
	$(MAKE) docker-metabase

docker-down:    ## Stop all services
	-docker compose -f services/db/compose.yaml down 2>/dev/null
	-docker compose -f services/pipeline/compose.yaml down 2>/dev/null
	-docker compose -f services/metabase/compose.yaml down 2>/dev/null

docker-logs:    ## Tail logs from all services
	@docker compose -f services/db/compose.yaml logs -f &
	@docker compose -f services/pipeline/compose.yaml logs -f &
	@docker compose -f services/metabase/compose.yaml logs -f &
	@wait

# === DOCKER - SERVICES =====================================================

docker-db:      ## Start Postgres 16
	docker compose -f services/db/compose.yaml up -d

docker-pipeline: ## Start pipeline worker
	docker compose -f services/pipeline/compose.yaml up -d

docker-metabase: ## Start Metabase
	docker compose -f services/metabase/compose.yaml up -d
