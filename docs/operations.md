# Operations Guide

## Ownership

- Web owners: public ingress, trusted proxy headers, same-origin routing, session boundary
- API owners: token authz, run CRUD, SSE, database migrations, API health
- Worker owners: execution, retries, lease heartbeats, worker health, model-provider secrets
- Platform owners: Postgres, OTEL collector, Prometheus, Grafana, Tempo, rollout orchestration

## Shared-Usage Rules

- Treat `apps/web` as the only public surface.
- Issue a dedicated server token for `apps/web`; do not reuse operator or admin tokens there.
- Keep `apps/api` and `apps/worker` on private networking only.
- Forward real user identity into `apps/web` with the trusted proxy headers instead of exposing raw backend tokens to browsers.
- Rotate worker model credentials independently from web and API credentials.

## Release Checklist

- `make ci` passed on the release commit
- `pnpm --dir apps/web test` passed
- `pnpm --dir apps/web build` passed
- database backup or snapshot completed
- rollback owner assigned
- canary env vars prepared for the target environment
- dashboard views for queue depth, worker heartbeat freshness, API errors, and terminal status mix are open

## Canary Procedure

1. Confirm `GET /health` on the API returns `status=ok`.
2. Confirm worker `/health` returns `status=ok`.
3. Run `make production-canary`.
4. Verify the canary run reaches `completed`.
5. Confirm the worker health payload shows a fresh heartbeat and a recent `last_finished_run_id`.
6. Check Grafana and traces for the canary run before increasing traffic.

## Incident Playbooks

### Same-Origin Routing Failure

Symptoms:

- the web app shows 401, 403, or 502 errors on `/api/runs`
- API health is green, but web-originated run creation fails

Actions:

1. Verify `AGENT_HARNESS_API_BASE_URL` and `AGENT_HARNESS_API_TOKEN` on `apps/web`.
2. Confirm the trusted proxy secret and forwarded headers match the ingress configuration.
3. Check whether the web release accidentally reintroduced stale or browser-visible API config.
4. Roll back the web release if the proxy boundary is broken.

### Worker Stalls

Symptoms:

- queued runs rise
- worker `/health` reports `stale`
- no new `run.started` events appear

Actions:

1. Check worker logs for lease refresh failures or provider hangs.
2. Confirm Postgres latency and connectivity from the worker fleet.
3. Restart stale workers.
4. Re-run the production canary before restoring normal traffic.

### Retry Storm

Symptoms:

- repeated `run.retry_scheduled` events
- worker health stays `ok`, but success rate drops

Actions:

1. Inspect the first failing run for a shared dependency or model-provider failure.
2. Pause traffic or disable the affected workflow source if failures are systemic.
3. Fix the dependency, provider config, or worker release.
4. Re-run a single canary before resuming traffic.

### Timeout Spike

Symptoms:

- `run.timeout_exceeded` events increase
- queue depth grows while worker health stays green

Actions:

1. Determine whether the bottleneck is provider latency, workflow code, or queue saturation.
2. Scale workers only if the backlog is from demand, not from a regression.
3. Increase timeout budgets only when the workflow remains bounded and the dependency is healthy.
4. Otherwise roll back and investigate.

### Auth Failure

Symptoms:

- 401 or 403 rates rise on `/api/runs` or `/metrics`
- canary run creation fails before the worker sees the run

Actions:

1. Verify `AGENT_HARNESS_API_TOKENS` on `apps/api`.
2. Verify `AGENT_HARNESS_API_TOKEN` on `apps/web`.
3. Confirm trusted proxy secret rotation completed on both the ingress and web service.
4. Restore the previous secret set if rotation was only partially deployed.

## Credential Rotation

Rotate secrets in this order to avoid breaking the public web path:

1. Add the new API token set to `apps/api` while keeping the old set valid.
2. Deploy `apps/web` with the new `AGENT_HARNESS_API_TOKEN`.
3. Remove the old API token from `apps/api`.
4. Rotate the trusted proxy secret across the ingress and `apps/web`.
5. Rotate worker model-provider secrets.
6. Run `make production-canary`.

## Minimum Monitoring

- queue depth
- run success rate
- retry count
- timeout count
- worker heartbeat freshness
- API 401/403/5xx rate
- Postgres connection saturation
- OTEL exporter error rate
