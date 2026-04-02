.PYTHON_LINT_PATHS = apps/api/src apps/worker/src packages/agent-core/src packages/contracts/src packages/contracts/scripts scripts/production_canary.py tests/test_backend_runtime.py tests/test_postgres_migrations.py

.PHONY: install install-python install-web lint typecheck-python typecheck-web typecheck test ci infra-up infra-down dev dev-api dev-worker dev-web production-canary stop

install: install-python install-web

install-python:
	uv sync

install-web:
	cd apps/web && pnpm install

lint:
	uv run ruff check $(.PYTHON_LINT_PATHS)

typecheck-python:
	uv run mypy

typecheck-web:
	cd apps/web && pnpm exec tsc --noEmit

typecheck: typecheck-python typecheck-web

test:
	uv run pytest

ci: lint typecheck test

dev-api:
	cd apps/api && uv run uvicorn agent_harness_api.main:app --reload

dev-worker:
	cd apps/worker && uv run python -m agent_harness_worker.main

dev-web:
	cd apps/web && pnpm dev

production-canary:
	uv run python scripts/production_canary.py

# Infrastructure
infra-up:
	cd infra/docker && docker compose up -d

infra-down:
	cd infra/docker && docker compose down

# Run everything
dev: infra-up
	@echo "Starting all services..."
	@trap 'kill 0' INT; \
	(cd apps/api && uv run uvicorn agent_harness_api.main:app --reload --port 8000) & \
	(cd apps/worker && uv run python -m agent_harness_worker.main) & \
	(cd apps/web && pnpm dev) & \
	wait

# Stop all services
stop: infra-down
	@echo "Stopped all services"
