# API

This FastAPI service is now the control plane for agent runs.

Current responsibilities:

- create durable runs
- list and inspect run state
- cancel queued or active runs
- stream structured run events over Server-Sent Events

Environment:

- `AGENT_HARNESS_DATABASE_URL`: Postgres connection string used for runtime storage and migrations
- `AGENT_HARNESS_API_TOKENS`: API token map in the format `<token>=<role>` with `viewer`, `operator`, and `admin` roles
- `AGENT_HARNESS_OTEL_EXPORTER_OTLP_ENDPOINT`: base OTLP HTTP endpoint used to ship traces to the collector
- `AGENT_HARNESS_CORS_ORIGINS`: allowed browser origins for the operator console

Key endpoints:

- `POST /runs`
- `GET /runs`
- `GET /runs/{run_id}`
- `POST /runs/{run_id}/cancel`
- `GET /runs/{run_id}/events/stream`
- `GET /metrics`

Access policy:

- `GET /runs`, `GET /runs/{run_id}`, and event streaming require at least a `viewer` token
- `POST /runs` and `POST /runs/{run_id}/cancel` require an `operator` token
- `GET /metrics` and API docs require an `admin` token
