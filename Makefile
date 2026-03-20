.PYTHON_LINT_PATHS = apps/api/src apps/worker/src packages/agent-core/src packages/contracts/src packages/contracts/scripts scripts/production_canary.py tests/test_backend_runtime.py tests/test_postgres_migrations.py

.PHONY: install-python install-web lint typecheck-python typecheck-web typecheck test ci dev-api dev-worker dev-web production-canary

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
