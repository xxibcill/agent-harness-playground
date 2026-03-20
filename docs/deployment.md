# Deployment Guide

## Scope

This repository now supports a repeatable release path for three services:

- `apps/api`: FastAPI control plane
- `apps/worker`: background executor
- `apps/web`: Next.js operator console

## Required Secrets

Set secrets through your deployment system. Do not commit populated `.env` files.

- `AGENT_HARNESS_DATABASE_URL`: Postgres connection string for runtime state
- `AGENT_HARNESS_API_TOKENS`: comma-separated API tokens in the format `<token>=<role>`
- `AGENT_HARNESS_OTEL_EXPORTER_OTLP_ENDPOINT`: OTLP collector endpoint
- `NEXT_PUBLIC_API_BASE_URL`: browser-visible API base URL

## Runtime Defaults

- New runs default to `3` attempts and a `300` second timeout.
- Workers refresh run leases every `10` seconds and retry failed work after `5` seconds by default.
- `viewer` tokens can read runs and event streams.
- `operator` tokens can create and cancel runs.
- `admin` tokens are required for `/metrics` and API docs.

## Pre-Release Checks

Run these checks before deploying:

```bash
make ci
pnpm --dir apps/web build
```

## Deployment Order

1. Apply environment changes and rotate tokens if needed.
2. Deploy `apps/api`.
3. Deploy `apps/worker`.
4. Deploy `apps/web`.
5. Create a canary run and confirm:
   - `POST /runs` succeeds with an operator token
   - the worker claims the run
   - the run completes or retries as expected
   - `/metrics` responds with an admin token

## Rollback

Rollback is safe because the new migration only adds nullable-compatible runtime columns with defaults.

1. Stop new traffic to the failing release.
2. Roll back application images to the last known good version.
3. Leave the database migration in place.
4. Re-run the canary checks above.
5. If the failure was caused by token or env changes, restore the previous secret set before re-enabling traffic.

## Production Caveat For The Current Web App

`apps/web` still calls the API directly from the browser. That is acceptable for a single trusted operator with a scoped token, but not for a multi-user production deployment. For shared production use, front the API with a trusted session-aware proxy or move API calls behind authenticated Next.js server routes.
