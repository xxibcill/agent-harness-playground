# Agent Harness Playground

This repository currently contains a small Python LangGraph prototype. The long-term direction is a single monorepo with a Python backend for agent execution and observability, plus a Next.js frontend that acts as a client for triggering runs and monitoring execution in a graphical UI.

## Current Status

- The current runnable prototype lives in `src/basic_langgraph_agent/`.
- Task 1 scaffolding has started with `apps/`, `packages/`, `infra/`, and `tasks/`.
- The prototype exposes a CLI agent flow backed by LangGraph and an Anthropic-compatible model endpoint.
- Token usage is written to `data/token_usage.jsonl`.
- Tests currently cover the prototype behavior in `tests/`.

## Long-Term Target

The target product is an agent platform with two major surfaces:

- Backend services that run AI agent workflows, persist run state, emit traces, and expose monitoring data.
- A Next.js frontend that starts runs, follows them live, and visualizes the agent workflow, node progress, traces, and operational metrics.

## Target Repository Structure

```text
apps/
  api/                FastAPI control plane for runs, sessions, and queries
  worker/             Background workers that execute LangGraph workflows
  web/                Next.js frontend for run creation and monitoring
packages/
  agent-core/         Shared workflow definitions, prompts, tools, and adapters
  observability/      Tracing, metrics, logging, and event helpers
  contracts/          Shared schemas, OpenAPI artifacts, and generated client types
infra/
  docker/             Local containers for Postgres, Redis, and object storage
  grafana/            Dashboards and provisioning
  prometheus/         Metrics scrape configuration
  otel/               OpenTelemetry Collector configuration
tasks/
  README.md           Ordered milestone list
  01-repo-foundation.md
  02-backend-runtime.md
  03-observability.md
  04-nextjs-client.md
  05-hardening-and-release.md
Makefile
package.json
pnpm-workspace.yaml
src/
  basic_langgraph_agent/
tests/
```

## Architecture Summary

### Frontend

- `apps/web` will be a Next.js application, likely using the App Router.
- It should remain a client-facing surface, not the home of agent orchestration logic.
- Its responsibilities are:
  - create and configure agent runs
  - show live workflow progress
  - render traces, logs, and metrics
  - provide operator-facing dashboards

### Backend Control Plane

- `apps/api` will expose HTTP and streaming interfaces for:
  - creating runs
  - listing and inspecting runs
  - cancelling runs
  - streaming live run events to the UI
- This service owns request validation, persistence, authorization, and read models for the frontend.

### Backend Execution Plane

- `apps/worker` will execute LangGraph workflows asynchronously.
- Workers should emit structured events for every meaningful runtime step:
  - run started
  - node started
  - node completed
  - tool started
  - tool completed
  - model call started
  - model call completed
  - run failed
  - run completed
- The existing prototype in `src/basic_langgraph_agent/` is the starting point for this execution layer and will eventually be migrated into `packages/agent-core`.

### Observability Plane

- Tracing should be built on OpenTelemetry.
- Metrics should be exported to Prometheus and visualized in Grafana.
- Distributed traces should be viewable in a trace backend such as Tempo.
- Structured logs and stored run events should share common identifiers such as `run_id`, `trace_id`, and `node_id`.

### Data Flow

1. A user submits a run from the Next.js frontend.
2. The API validates the request, creates a durable run record, and queues work.
3. A worker picks up the run and executes the LangGraph workflow.
4. The worker emits structured events, traces, logs, and usage metrics during execution.
5. The API streams run updates back to the frontend.
6. The frontend renders the live graph, trace timeline, and monitoring dashboards from those backend signals.

## Proposed Core Components

- `Postgres` for durable run state, event history, workflow metadata, and reporting queries
- `Redis` for queueing and transient event fan-out
- `FastAPI` for backend APIs
- `LangGraph` for workflow orchestration
- `Next.js` for the frontend client
- `OpenTelemetry` for tracing instrumentation
- `Prometheus`, `Grafana`, and `Tempo` for monitoring and trace visualization

## Design Rules

- The frontend is a client surface only. Agent execution stays in Python services.
- The UI should render explicit backend events instead of inferring state from plain logs.
- Every run should be traceable end to end with consistent identifiers.
- Shared request and response contracts should be generated, not duplicated by hand.
- The current prototype should keep working while the monorepo structure is introduced gradually.

## Roadmap

The implementation roadmap is broken into small milestone documents under `tasks/`.

- `tasks/README.md`
- `tasks/01-repo-foundation.md`
- `tasks/02-backend-runtime.md`
- `tasks/03-observability.md`
- `tasks/04-nextjs-client.md`
- `tasks/05-hardening-and-release.md`

## Current Prototype Commands

Until the migration starts, the current prototype still uses the existing Python workflow.

```bash
uv sync
uv run basic-agent --model "your_provider_model" "Say hello to LangGraph"
uv run basic-agent-usage
uv run pytest
```

## Task 1 Scaffold Commands

These commands support the new foundation that now exists in the repository.

```bash
make install-python
make install-web
make lint
make typecheck
make test
make ci
make dev-api
make dev-worker
make dev-web
```

## Hardening And Release

Task 05 adds production-minded controls around the runtime:

- API token authentication with `viewer`, `operator`, and `admin` roles
- per-run timeout and retry policy persisted with runtime state
- worker lease heartbeats and retry backoff handling
- CI for lint, type checks, tests, and Postgres-backed migration safety

Release and operations documentation:

- `docs/deployment.md`
- `docs/operations.md`
