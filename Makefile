# Joseph platform task runner. `make help` lists targets.
# Requires: uv (Python 3.12+), Node 20+ with corepack (pnpm 10).

SHELL := /bin/bash
export COREPACK_ENABLE_DOWNLOAD_PROMPT := 0
ME := joseph-market-engine
EE := joseph-execution-engine
TERM_DIR := joseph-terminal
DEV_TOKEN ?= dev-local-token
SERVICE_TOKEN ?= dev-service-token

.PHONY: help setup dev dev-market dev-execution dev-web lint typecheck \
        test test-python test-terminal test-system test-e2e test-network \
        test-sandbox \
        contracts openapi ci images compose-up compose-down clean

help: ## Show targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-16s %s\n", $$1, $$2}'

setup: ## Install every dependency (locked versions)
	cd $(ME) && uv sync --frozen
	cd $(EE) && uv sync --frozen
	cd system-tests && uv sync --frozen
	cd $(TERM_DIR) && pnpm install --frozen-lockfile

dev-market: ## Run the market engine on :8000 (all venues; DEMO venue included)
	cd $(ME) && mkdir -p var && \
	JOSEPH_DEV_TOKENS=$(DEV_TOKEN):tenant-01:user-01 \
	JOSEPH_SERVICE_TOKEN=$(SERVICE_TOKEN) \
	uv run uvicorn joseph.bootstrap:create_application --factory --port 8000

dev-execution: ## Run the execution engine on :8100 (paper trading; LIVE off)
	cd $(EE) && mkdir -p var && \
	JOSEPH_DEV_TOKENS=$(DEV_TOKEN):tenant-01:user-01 \
	JOSEPH_SERVICE_TOKEN=$(SERVICE_TOKEN) JOSEPH_MARKET_GATEWAY=http \
	JOSEPH_MARKET_URL=http://localhost:8000 \
	uv run uvicorn joseph_execution.bootstrap:create_application --factory --port 8100

dev-web: ## Run the terminal on :3000 against the local engines
	cd $(TERM_DIR) && \
	NEXT_PUBLIC_MARKET_API_URL=http://localhost:8000 \
	NEXT_PUBLIC_MARKET_WS_URL=ws://localhost:8000 \
	NEXT_PUBLIC_EXECUTION_API_URL=http://localhost:8100 \
	NEXT_PUBLIC_EXECUTION_WS_URL=ws://localhost:8100 \
	NEXT_PUBLIC_DEV_TOKEN=$(DEV_TOKEN) pnpm dev

dev: ## Run all three services (Ctrl-C stops them)
	@trap 'kill 0' INT TERM; \
	$(MAKE) --no-print-directory dev-market & sleep 4; \
	$(MAKE) --no-print-directory dev-execution & \
	$(MAKE) --no-print-directory dev-web & wait

lint: ## Format checks and Google-style linters on every project
	cd $(ME) && uv run ruff format --check src tests scripts && uv run ruff check src tests scripts
	cd $(EE) && uv run ruff format --check src tests scripts && uv run ruff check src tests scripts
	cd $(TERM_DIR) && pnpm format:check && pnpm lint

typecheck: ## mypy --strict and tsc
	cd $(ME) && uv run mypy
	cd $(EE) && uv run mypy
	cd $(TERM_DIR) && pnpm typecheck

test-python: ## Unit/integration/contract/architecture/security/chaos/property (both engines)
	cd $(ME) && uv run pytest
	cd $(EE) && uv run pytest

test-terminal: ## Vitest (unit, component, contract, architecture) + boundary/secret/license scans
	cd $(TERM_DIR) && pnpm contracts:check && pnpm test && pnpm arch:check && pnpm secrets:scan && pnpm licenses:check

test-system: ## Cross-service system tests (boots both engines)
	cd system-tests && uv run pytest

test-e2e: ## Browser system tests + accessibility (DEMO mode)
	cd $(TERM_DIR) && pnpm build && pnpm test:e2e

test-network: ## Opt-in live venue tests against real public APIs
	cd $(ME) && JOSEPH_NETWORK_TESTS=1 uv run pytest tests/network

test-sandbox: ## Opt-in testnet order round trips (needs JOSEPH_SANDBOX_<VENUE>_* keys)
	cd $(EE) && JOSEPH_SANDBOX_TESTS=1 uv run pytest -m sandbox tests/sandbox -s

test: test-python test-terminal test-system test-e2e ## Every offline test tier

contracts: ## Regenerate TypeScript types from JSON Schemas
	cd $(TERM_DIR) && pnpm contracts:gen

openapi: ## Regenerate OpenAPI documents from both engines
	cd $(ME) && uv run python scripts/export_openapi.py
	cd $(EE) && uv run python scripts/export_openapi.py

ci: lint typecheck test ## Everything CI runs
	cd $(ME) && uv run python scripts/export_openapi.py --check
	cd $(EE) && uv run python scripts/export_openapi.py --check

# PineScripts access for the market-engine build: the token in
# PINESCRIPTS_TOKEN if set, else the local ssh-agent.
comma := ,
PINESCRIPTS_ACCESS = $(if $(PINESCRIPTS_TOKEN),--secret id=pinescripts_token$(comma)env=PINESCRIPTS_TOKEN,--ssh default)

images: ## Build the three production container images locally
	docker build $(PINESCRIPTS_ACCESS) -t joseph-market-engine:local $(ME)
	docker build -t joseph-execution-engine:local $(EE)
	docker build -t joseph-terminal:local $(TERM_DIR)

compose-up: ## Build and run the full stack in containers (needs deploy/.env)
	docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d --build --wait

compose-down: ## Stop the container stack (volumes kept)
	docker compose -f deploy/docker-compose.yml --env-file deploy/.env down

clean: ## Remove local databases and build output
	rm -rf $(ME)/var $(EE)/var $(TERM_DIR)/apps/web/.next
