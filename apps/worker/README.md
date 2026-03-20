# Worker

This service is the execution plane for queued agent runs.

Current responsibilities:

- claim queued runs with a lease so stale work can be recovered
- execute LangGraph workflows asynchronously
- persist workflow, node, tool, and model events
- mark runs as completed, failed, or cancelled

Environment:

- `AGENT_HARNESS_DATABASE_URL`: Postgres connection string used for queue and runtime storage
- `AGENT_HARNESS_WORKER_ID`: worker identity recorded on claimed runs
- `AGENT_HARNESS_WORKER_POLL_SECONDS`: idle polling interval
- `AGENT_HARNESS_WORKER_LEASE_SECONDS`: lease duration before stale runs can be reclaimed
- `AGENT_HARNESS_WORKER_LEASE_REFRESH_SECONDS`: heartbeat interval used to renew active leases
- `AGENT_HARNESS_WORKER_METRICS_PORT`: Prometheus scrape port for worker metrics
- `AGENT_HARNESS_WORKER_RETRY_BACKOFF_SECONDS`: base delay before retrying a failed run
- `AGENT_HARNESS_OTEL_EXPORTER_OTLP_ENDPOINT`: base OTLP HTTP endpoint used to ship traces to the collector

Runtime policy:

- workers retry non-cancelled failures until `max_attempts` is exhausted
- runs that exceed `timeout_seconds` emit `run.timeout_exceeded`
- retry scheduling is persisted as `run.retry_scheduled`
