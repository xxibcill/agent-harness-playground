# Deployment Guide

## Target Topology

Deploy the platform as one public web surface with private execution services behind it.

| Service | Exposure | Responsibility |
| --- | --- | --- |
| `apps/web` | public | operator UI, same-origin `/api/runs` proxy, session boundary |
| `apps/api` | private | run persistence, authz, read APIs, SSE, metrics, API health |
| `apps/worker` | private | queued run execution, retries, lease heartbeats, worker health, metrics |
| Postgres | private | durable run state, events, retry scheduling, leases |
| OTEL collector / Prometheus / Grafana / Tempo | private | traces, metrics, alerting, dashboards |

Recommended network policy:

- Expose only the web ingress to end users.
- Allow `apps/web` to reach `apps/api` over a private network.
- Allow `apps/api` and `apps/worker` to reach Postgres and observability backends.
- Keep the worker health and metrics ports internal-only.

## Standard Environment Variables

### Web

- `AGENT_HARNESS_API_BASE_URL`: private base URL for `apps/api`
- `AGENT_HARNESS_API_TOKEN`: server-side API token used by the Next.js proxy
- `AGENT_HARNESS_WEB_TRUSTED_PROXY_SECRET`: shared secret required when an upstream ingress forwards user identity
- `AGENT_HARNESS_WEB_TRUSTED_PROXY_SECRET_HEADER`: optional override for the trusted proxy secret header
- `AGENT_HARNESS_WEB_TRUSTED_PROXY_USER_HEADER`: optional override for the forwarded user header
- `AGENT_HARNESS_WEB_TRUSTED_PROXY_ROLE_HEADER`: optional override for the forwarded role header
- `AGENT_HARNESS_WEB_DEV_ROLE`: local-development fallback role when trusted proxy mode is disabled

Production note: `apps/web` no longer reads `NEXT_PUBLIC_API_BASE_URL` or `NEXT_PUBLIC_API_TOKEN`.
Backend routing and credentials stay server-side.

### API

- `AGENT_HARNESS_DATABASE_URL`: Postgres connection string
- `AGENT_HARNESS_API_TOKENS`: comma-separated token map in the format `<token>=<role>`
- `AGENT_HARNESS_CORS_ORIGINS`: allowed web origins when browser access is needed
- `AGENT_HARNESS_OTEL_EXPORTER_OTLP_ENDPOINT`: OTLP HTTP endpoint for traces

Token guidance:

- Use a dedicated server token for `apps/web`.
- Keep `viewer`, `operator`, and `admin` tokens distinct.
- Do not expose API tokens to browsers.

### Worker

- `AGENT_HARNESS_DATABASE_URL`: Postgres connection string
- `AGENT_HARNESS_WORKER_ID`: logical worker identity
- `AGENT_HARNESS_WORKER_POLL_SECONDS`: idle queue polling interval
- `AGENT_HARNESS_WORKER_LEASE_SECONDS`: run lease duration
- `AGENT_HARNESS_WORKER_LEASE_REFRESH_SECONDS`: lease heartbeat interval
- `AGENT_HARNESS_WORKER_RETRY_BACKOFF_SECONDS`: retry backoff base
- `AGENT_HARNESS_WORKER_METRICS_PORT`: Prometheus scrape port
- `AGENT_HARNESS_WORKER_HEALTH_HOST`: bind host for `/health`
- `AGENT_HARNESS_WORKER_HEALTH_PORT`: bind port for `/health`
- `AGENT_HARNESS_WORKER_HEALTH_STALE_SECONDS`: max allowed time since the last worker heartbeat before the health endpoint reports `stale`
- `AGENT_HARNESS_OTEL_EXPORTER_OTLP_ENDPOINT`: OTLP HTTP endpoint for traces

Model-backed workflow secrets live on workers, not in the web or browser layer:

- `ANTHROPIC_AUTH_TOKEN` or `ANTHROPIC_API_KEY`
- `ANTHROPIC_MODEL`
- `ANTHROPIC_MAX_TOKENS`
- `ANTHROPIC_BASE_URL`
- `API_TIMEOUT_MS`

## Health Checks

Configure readiness and liveness probes with these endpoints:

- Web ingress: `GET /` or your platform-native HTTP readiness check
- API: `GET /health`
- Worker: `GET http://<private-worker-host>:<AGENT_HARNESS_WORKER_HEALTH_PORT>/health`
- Metrics:
  - API: `GET /metrics` with an `admin` token
  - Worker: `GET http://<private-worker-host>:<AGENT_HARNESS_WORKER_METRICS_PORT>/metrics`

The worker health endpoint returns the current worker id, the most recent heartbeat timestamp, and
the last finished run id so rollout automation can distinguish between "process up" and "worker
actively making progress."

## Deployment Order

1. Apply database, token, and model-secret changes.
2. Deploy `apps/api`.
3. Deploy `apps/worker`.
4. Confirm API and worker health checks are green.
5. Deploy `apps/web`.
6. Run the same-origin canary through the web surface.

## Production Canary

Run the canary from an environment that can reach the public web URL, the private API health URL,
and the private worker health URL.

```bash
AGENT_HARNESS_CANARY_WEB_BASE_URL=https://agent.example.com \
AGENT_HARNESS_CANARY_API_BASE_URL=http://agent-api.internal:8000 \
AGENT_HARNESS_CANARY_WORKER_HEALTH_URL=http://agent-worker.internal:9102/health \
AGENT_HARNESS_CANARY_PROXY_SECRET=replace-me \
make production-canary
```

Optional overrides:

- `AGENT_HARNESS_CANARY_WORKFLOW`: defaults to `demo.echo`
- `AGENT_HARNESS_CANARY_INPUT`: custom canary input text
- `AGENT_HARNESS_CANARY_WORKFLOW_CONFIG_JSON`: JSON object for model-backed workflow config
- `AGENT_HARNESS_CANARY_TIMEOUT_SECONDS`: max wait for terminal completion
- `AGENT_HARNESS_CANARY_POLL_SECONDS`: polling interval
- `AGENT_HARNESS_CANARY_USER`, `AGENT_HARNESS_CANARY_ROLE`: forwarded operator identity
- `AGENT_HARNESS_CANARY_PROXY_SECRET_HEADER`, `AGENT_HARNESS_CANARY_USER_HEADER`, `AGENT_HARNESS_CANARY_ROLE_HEADER`: header overrides for non-default ingress wiring

The canary verifies:

- API health is `ok`
- worker health is `ok`
- a run can be created through the public web `/api/runs` route
- the worker claims and completes the run
- the event stream exposes `run.started` and `run.completed`

Use `demo.echo` for bootstrap environments. Switch to a model-backed workflow only after worker
provider secrets have been provisioned.

## Rollout And Rollback

### Canary rollout

1. Shift a small slice of operator traffic to the new web release.
2. Run `make production-canary`.
3. Check Grafana for queue depth, worker heartbeat freshness, error rate, and trace ingestion.
4. Increase traffic only after the canary is green and metrics are stable.

### Rollback

1. Stop new traffic to the failing web or API release.
2. Roll back `apps/web`, `apps/api`, or `apps/worker` to the last known good image.
3. Leave additive database migrations in place unless a migration-specific rollback is documented.
4. Restore the previous token or model-secret set if the incident was caused by secret rotation.
5. Re-run `make production-canary` before restoring normal traffic.
