.PHONY: install-python install-web test dev-api dev-worker dev-web

install-python:
	uv sync

install-web:
	cd apps/web && pnpm install

test:
	uv run pytest

dev-api:
	cd apps/api && uv run uvicorn agent_harness_api.main:app --reload

dev-worker:
	cd apps/worker && uv run python -m agent_harness_worker.main

dev-web:
	cd apps/web && pnpm dev

