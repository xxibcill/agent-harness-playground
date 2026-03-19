# API

This FastAPI service is now the control plane for agent runs.

Current responsibilities:

- create durable runs
- list and inspect run state
- cancel queued or active runs
- stream structured run events over Server-Sent Events

Environment:

- `AGENT_HARNESS_DATABASE_URL`: Postgres connection string used for runtime storage and migrations

Key endpoints:

- `POST /runs`
- `GET /runs`
- `GET /runs/{run_id}`
- `POST /runs/{run_id}/cancel`
- `GET /runs/{run_id}/events/stream`
