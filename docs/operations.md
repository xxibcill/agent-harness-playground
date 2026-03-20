# Operations Guide

## Ownership

- API owners: authentication, run creation, cancellation, read paths
- Worker owners: run execution, retries, lease heartbeats, timeout behavior
- Platform owners: Postgres, OpenTelemetry pipeline, Prometheus, Grafana, release cadence

## Backup And Retention Policy

- Postgres runtime tables: nightly logical backup and pre-release backup
- Postgres retention: 30 daily backups, 12 monthly backups
- Prometheus metrics retention: 14 days
- Tempo trace retention: 7 days
- Structured logs retention: 30 days
- Run event history retention target: 90 days unless compliance requires longer

## Release-Day Checks

- Queue depth near zero before deployment
- No active incident on Postgres, OTEL collector, or worker fleet
- `make ci` and web build completed on the release commit
- A rollback owner is assigned

## Incident Playbooks

### Worker Stalls

Symptoms:
- queued runs rise
- no new `run.started` events
- leases expire and reclaim attempts increase

Actions:
1. Check worker metrics and logs for heartbeat failures.
2. Restart unhealthy workers.
3. Confirm runs are being reclaimed instead of remaining stuck.
4. If reclaim churn continues, reduce traffic and inspect Postgres latency.

### Retry Storm

Symptoms:
- repeated `run.retry_scheduled` events
- rising failure count with the same error payload

Actions:
1. Inspect the first failing attempt for a shared dependency failure.
2. Pause traffic or disable the broken workflow input source.
3. Fix the dependency or deploy a hotfix.
4. Re-run a single canary before restoring normal traffic.

### Timeout Spike

Symptoms:
- `run.timeout_exceeded` events increase
- success rate drops while queue depth grows

Actions:
1. Check whether the workflow or an external dependency slowed down.
2. Scale workers if the issue is queue contention.
3. Increase timeout budgets only if the workflow is still healthy and bounded.
4. If not, roll back the release and investigate the regression.

### Auth Failure

Symptoms:
- 401 or 403 rates rise on `/runs` or `/metrics`

Actions:
1. Verify token rotation timing across API and operator surfaces.
2. Confirm role mappings in `AGENT_HARNESS_API_TOKENS`.
3. Restore the previous token set if rotation was incomplete.

## Dashboard Minimums

- queue depth
- run success rate
- retry count
- timeout count
- worker heartbeat freshness
- Postgres connection saturation
