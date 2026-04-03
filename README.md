# Agent Harness Playground

This is an **educational project** for learning agent workflow patterns step by step.

Each workflow introduces exactly one new concept on top of the previous one, building from a trivial echo to a full ReAct loop. The repo is a working agent runtime monorepo -- not a scaffold -- so you can run every workflow end-to-end and observe real events in the dashboard.

It contains:

- a FastAPI control plane for durable agent runs
- a Python worker that claims queued runs from Postgres and executes workflows
- a Next.js operator console that launches runs and follows them over SSE
- shared Python packages for runtime logic, contracts, and observability
- a sequence of **seven teaching workflows** that progress from simple to advanced

## Quick Start

If you only want to get the project running locally, use this path.

### 1. Install the required tools

- Python 3.11+
- `uv`
- Node.js compatible with Next 15
- `pnpm`
- Docker

### 2. Install dependencies

```bash
make install
```

### 3. Start the full local stack

```bash
make dev
```

That target:

- starts Postgres and the local observability stack with Docker
- starts the FastAPI API on `http://127.0.0.1:8000`
- starts the worker
- starts the Next.js app on `http://127.0.0.1:3000`

### 4. Open the app

- Web UI: `http://127.0.0.1:3000`
- Grafana: `http://127.0.0.1:3001`

### 5. Verify the happy path

1. Open the dashboard in the web UI.
2. Create a run with the `demo.echo` workflow.
3. Enter any prompt and submit it.
4. Open the run details page and confirm that live events appear.

`demo.echo` does not require any external API keys, so it is the fastest way to confirm the project is working.

### Optional: enable auth locally

You do not need auth tokens for the default local setup. If `AGENT_HARNESS_API_TOKENS` is unset, the API runs in no-auth mode and the web app uses its development session.

If you want to test the token-based path locally, export these before `make dev`:

```bash
export AGENT_HARNESS_API_TOKENS=local-dev-token=operator
export AGENT_HARNESS_API_TOKEN=local-dev-token
```

### Optional: enable the Anthropic workflow

The `anthropic.respond` workflow (step 7 in the learning ladder) needs provider credentials. See the setup instructions in the [Workflows](#7-anthropicrespond----external-model-integration) section below.

### Stop everything

Press `Ctrl-C` in the `make dev` terminal, then shut down Docker services:

```bash
make infra-down
```

## What Runs Today

The platform ships **seven workflows** designed as a learning ladder. Run them in this order -- each one adds exactly one concept.

| # | Workflow | New concept | Needs API key? |
|---|----------|-------------|----------------|
| 1 | `demo.echo` | Direct response | No |
| 2 | `demo.route` | Conditional branching | No |
| 3 | `demo.tool.single` | Tool invocation as a graph node | No |
| 4 | `demo.tool.select` | Choosing among multiple tools | No |
| 5 | `demo.react.once` | One-shot reason then act | No |
| 6 | `demo.react` | Looping ReAct cycle | No |
| 7 | `anthropic.respond` | External model integration | Yes |

Workflows 1--6 use deterministic local logic, so you can experiment without any provider credentials. `anthropic.respond` is the first workflow that calls an external LLM.

Runs are durable. The API writes them to Postgres, the worker claims them with a lease, execution emits structured events, and the web app renders both historical state and live updates.

### Recommended run order

Start the stack with `make dev`, open `http://127.0.0.1:3000`, and try these prompts in order:

1. **`demo.echo`** -- type anything, e.g. `hello world`. Confirms the stack works end-to-end.
2. **`demo.route`** -- try `hi there`, `what is 2+2?`, `list my files`, or `the sky is blue`. Watch the router classify each input.
3. **`demo.tool.single`** -- enter `15 % 4`. The workflow always calls its one calculator tool.
4. **`demo.tool.select`** -- try `calculate 12 * 7` then `tell me the time`. The workflow inspects the input to pick the right tool.
5. **`demo.react.once`** -- enter `what is 25 / 5?`. The agent reasons once, picks a tool, executes it, and responds immediately.
6. **`demo.react`** -- enter `add 3 and 5, then multiply the result by 2`. The agent loops through reasoning and tool calls until it has the final answer.
7. **`anthropic.respond`** -- requires an Anthropic API key (see setup below). Any prompt will be forwarded to the model.

## Architecture

```mermaid
flowchart LR
    Browser["Browser"]
    Web["apps/web<br/>Next.js operator console"]
    Api["apps/api<br/>FastAPI control plane"]
    Db["Postgres<br/>runtime_runs + runtime_run_events + runtime_run_usage"]
    Worker["apps/worker<br/>polling executor"]
    OTel["OTEL / Prometheus / Grafana / Tempo"]

    Browser --> Web
    Web -->|same-origin /api/runs| Api
    Api --> Db
    Worker --> Db
    Worker --> OTel
    Api --> OTel
    Api -->|SSE stream| Web
```

### End-to-end flow

1. The browser submits a run to `apps/web` at `/api/runs`.
2. Next.js proxies that request to `apps/api` with server-side credentials.
3. The API authorizes the request, applies migrations on startup, and inserts a run into Postgres.
4. The worker polls Postgres, claims the next runnable row, and starts a lease heartbeat.
5. `RuntimeExecutor` runs the workflow and appends `run.*`, `workflow.*`, `node.*`, `tool.*`, and `model.*` events.
6. The API streams stored events as Server-Sent Events from `/runs/{run_id}/events/stream`.
7. The run details page hydrates from stored history, then switches to live SSE updates until the run reaches a terminal state.

## Repository Map

```text
apps/
  api/        FastAPI control plane
  web/        Next.js operator console and same-origin API proxy
  worker/     Polling worker and private health endpoint
docs/
  deployment.md   Current deployment topology and canary flow
  operations.md   Current runbook and incident guidance
infra/
  docker/     Local Postgres + observability stack
  grafana/
  otel/
  prometheus/
packages/
  agent-core/       Runtime executor, workflows, Postgres store, migrations
  contracts/        Shared Pydantic models and TS contract generator
  observability/    OTEL, Prometheus, logging correlation helpers
scripts/
  production_canary.py
tasks/
  Milestone docs and roadmap history
tests/
  Python tests for current backend runtime and workflow helpers
```

## Service And Package Guide

### `apps/api`

The API is the control plane. The main app lives in `apps/api/src/agent_harness_api/main.py`.

Current responsibilities:

- apply database migrations at startup
- create, list, fetch, and cancel runs
- expose run events as SSE
- enforce token-based role checks
- expose Prometheus metrics at `/metrics`

Important endpoints:

- `GET /health`
- `POST /runs`
- `GET /runs`
- `GET /runs/{run_id}`
- `POST /runs/{run_id}/cancel`
- `GET /runs/{run_id}/events/stream`
- `GET /metrics`

Authorization behavior:

- `viewer` can read runs and event streams
- `operator` can create and cancel runs
- `admin` can access `/metrics` and API docs
- if `AGENT_HARNESS_API_TOKENS` is unset, the API falls back to a no-auth mode and treats requests as admin

### `apps/worker`

The worker is the execution plane. The main loop lives in `apps/worker/src/agent_harness_worker/main.py`.

Current responsibilities:

- poll Postgres for runnable work
- claim runs with a lease so stale work can be recovered
- refresh the lease in a heartbeat thread while a run is active
- execute workflows through `RuntimeExecutor`
- persist retry scheduling, failure events, and terminal states
- expose worker Prometheus metrics and a private `/health` endpoint

Failure behavior:

- cancellation raises `ExecutionCancelled`
- timeout raises `ExecutionTimedOut`
- transient provider failures are retried up to `max_attempts`
- retry delay is persisted in Postgres as a future `scheduled_at`

### `apps/web`

The web app is a real operator surface now, not just a placeholder.

Current responsibilities:

- create runs from a dashboard form
- list recent runs
- show per-run detail pages
- fetch historical events
- follow live events via SSE
- proxy browser requests through same-origin `/api/runs` routes

Important implementation detail:

- the browser does not talk directly to the FastAPI service
- `apps/web/lib/server/api-proxy.ts` forwards requests with server-side credentials
- trusted-proxy mode can require upstream identity headers for multi-user deployments
- when trusted-proxy mode is off, local development falls back to a development session role, defaulting to `admin`

### `packages/agent-core`

This is the runtime heart of the current system.

Key pieces:

- `runtime.py`: `RunStore` protocol, `InMemoryRunStore`, `PostgresRunStore`, migrations
- `executor.py`: `RuntimeExecutor`, timeout handling, event emission, run output shaping
- `workflows/registry.py`: workflow registration
- `workflows/demo_echo.py`: demo workflow
- `workflows/anthropic.py`: Anthropic-compatible workflow and provider config loading
- `usage_tracker.py`: JSONL token usage helpers used by the Anthropic workflow

### `packages/contracts`

Shared Pydantic models used by API, worker, and tests.

Important models:

- `CreateRunRequest`
- `RunRecord`
- `RunEvent`
- `WorkflowConfig`
- `TokenUsage`

The repo also generates TypeScript types for the web app from these Python models:

- source: `packages/contracts/scripts/generate_frontend_types.py`
- output: `apps/web/lib/generated/contracts.ts`

### `packages/observability`

Shared observability primitives used by both API and worker.

Provides:

- OTEL tracer setup
- trace context propagation helpers
- Prometheus metric registration
- log correlation via `run_id` and `trace_id`

## Runtime Model

### Run lifecycle

Run statuses are:

- `queued`
- `running`
- `cancelling`
- `completed`
- `failed`
- `cancelled`

### Database schema

The durable runtime currently lives in Postgres.

Main tables:

- `runtime_runs`: run state, retry policy, workflow config, lease data, trace context
- `runtime_run_events`: ordered event log per run
- `runtime_run_usage`: token usage extracted from `model.completed` events
- `schema_migrations`: applied SQL migrations

Current migrations live in:

- `packages/agent-core/src/agent_harness_core/migrations/001_runtime_tables.sql`
- `packages/agent-core/src/agent_harness_core/migrations/002_observability.sql`
- `packages/agent-core/src/agent_harness_core/migrations/003_run_policies.sql`
- `packages/agent-core/src/agent_harness_core/migrations/004_workflow_config.sql`

### Event taxonomy

The executor emits structured event types such as:

- `run.created`
- `run.queued`
- `run.started`
- `run.retry_scheduled`
- `run.completed`
- `run.failed`
- `run.cancel_requested`
- `run.cancelled`
- `workflow.started`
- `workflow.completed`
- `node.started`
- `node.completed`
- `tool.started`
- `tool.completed`
- `model.started`
- `model.completed`
- `model.failed`

The web UI reads these events directly to build its timeline and workflow graph.

## Workflows

### 1. `demo.echo` -- Direct Response

Defined in `packages/agent-core/src/agent_harness_core/workflows/demo_echo.py`.

The simplest smoke test. No branching, no tools, no external calls. Given an input string it normalizes whitespace and returns `"Echo: <input>"`.

**What this teaches:** the basic shape of a workflow -- receive input, produce output, track token usage -- with zero moving parts. If `demo.echo` passes, the runtime infrastructure is working.

**Try:** `hello world`

### 2. `demo.route` -- Conditional Branching

Defined in `packages/agent-core/src/agent_harness_core/workflows/demo_route.py`.

Introduces a LangGraph `StateGraph` with conditional edges. A `classify` node sorts the input into one of four categories (greeting, question, command, statement), then routes to the matching response node.

**What this teaches:** how to branch inside a graph without yet thinking about tool calls. The only new idea is *conditional routing*.

**Try:** `hi there` / `what is 2+2?` / `list my files` / `the sky is blue`

### 3. `demo.tool.single` -- Single Tool Execution

Defined in `packages/agent-core/src/agent_harness_core/workflows/demo_tool_single.py`.

Calls exactly one deterministic tool (a calculator) embedded in the graph. No selection logic -- the tool is always invoked.

**What this teaches:** how a tool node fits into the graph. The only new idea is *tool invocation as a graph node*.

**Try:** `15 % 4`

### 4. `demo.tool.select` -- Choosing Among Multiple Tools

Defined in `packages/agent-core/src/agent_harness_core/workflows/demo_tool_select.py`.

Registers several tools and adds a selection step that inspects the input to pick the right one. Execution is still one-shot: pick a tool, call it, respond.

**What this teaches:** *tool selection* -- inspecting input to decide which tool to use. No looping yet.

**Try:** `calculate 12 * 7` then `tell me the time`

### 5. `demo.react.once` -- One-shot Reason Then Act

Defined in `packages/agent-core/src/agent_harness_core/workflows/demo_react_once.py`.

Combines reasoning and tool selection into a single plan-then-execute pass. The agent reasons about what to do, selects a tool, executes it, and responds immediately -- no iteration.

**What this teaches:** the *reason-act pattern* in its simplest one-pass form.

**Try:** `what is 25 / 5?`

### 6. `demo.react` -- Looping ReAct Cycle

Defined in `packages/agent-core/src/agent_harness_core/workflows/react.py`.

Extends the one-shot pattern into a loop: reason, act, observe the result, then reason again. The graph cycles through reason and tool nodes until no further tool call is needed, then responds.

**What this teaches:** *iteration inside a workflow graph* -- the core ReAct loop. Unlike `demo.react.once`, this workflow can use multiple tools in sequence by looping back through reasoning after each tool result.

**Try:** `add 3 and 5, then multiply the result by 2`

### 7. `anthropic.respond` -- External Model Integration

Defined in `packages/agent-core/src/agent_harness_core/workflows/anthropic.py`.

The first workflow that calls an external LLM. Uses the Anthropic Messages API to generate a response. Every concept before this point used deterministic logic; this step introduces a *real model* as the reasoning engine.

**What this teaches:** how to wire a provider API call into the same workflow abstraction. The only new idea is *external model integration*.

**Setup:** requires provider credentials. Export these before `make dev`:

```bash
export ANTHROPIC_AUTH_TOKEN=your-token
export ANTHROPIC_MODEL=your-model-id
```

**Try:** any prompt -- it will be forwarded to the model.

The worker, not the browser or web app, owns the provider credentials.

## Local Development

Use `make dev` if you want the quickest setup. Use the steps below if you want to run services separately or override the defaults.

### Install

```bash
make install
```

Equivalent commands:

```bash
uv sync
cd apps/web && pnpm install
```

### Start infrastructure only

```bash
make infra-up
```

That stack includes:

- Postgres
- Tempo
- OTEL Collector
- Prometheus
- Grafana
- Redis

Redis is present in the compose file but the current runtime still uses Postgres directly for queueing.

### Minimal local configuration

For the default local setup, you usually do not need to export anything:

- Postgres defaults to `postgresql://agent_harness:agent_harness@127.0.0.1:5432/agent_harness`
- the web app defaults to `http://127.0.0.1:8000` for the API
- the API falls back to no-auth mode if `AGENT_HARNESS_API_TOKENS` is unset
- the web app falls back to a local `admin` development session if trusted proxy mode is off

Useful variables when you do want to override behavior:

- `AGENT_HARNESS_DATABASE_URL`
- `AGENT_HARNESS_API_BASE_URL`
- `AGENT_HARNESS_API_TOKENS`
- `AGENT_HARNESS_API_TOKEN`
- `AGENT_HARNESS_WEB_TRUSTED_PROXY_SECRET`
- `AGENT_HARNESS_WEB_DEV_ROLE`
- `ANTHROPIC_AUTH_TOKEN` or `ANTHROPIC_API_KEY`
- `ANTHROPIC_MODEL`
- `ANTHROPIC_MAX_TOKENS`
- `ANTHROPIC_BASE_URL`
- `API_TIMEOUT_MS`

Notes:

- the Python services do not automatically load `.env` on startup
- the Anthropic workflow helper does read the project `.env` file for provider config if one exists
- for full per-service environment details, see `apps/api/README.md`, `apps/worker/README.md`, and `apps/web/README.md`

### Start services separately

Run each service in its own terminal:

```bash
make dev-api
make dev-worker
make dev-web
```

Defaults:

- API: `http://127.0.0.1:8000`
- Web: `http://127.0.0.1:3000`
- Worker metrics: `http://127.0.0.1:9101/metrics`
- Worker health: `http://127.0.0.1:9102/health`
- Grafana: `http://127.0.0.1:3001`

## Commands

Root `Makefile` targets:

- `make install-python`
- `make install-web`
- `make lint`
- `make typecheck-python`
- `make typecheck-web`
- `make typecheck`
- `make test`
- `make ci`
- `make dev-api`
- `make dev-worker`
- `make dev-web`
- `make production-canary`

Useful direct commands:

```bash
uv run pytest
pnpm --dir apps/web test
pnpm --dir apps/web build
uv run python -m agent_harness_core.usage_tracker
```

## Testing

The test surface is split by runtime:

- `tests/test_backend_runtime.py`: current API, worker, workflows, auth, traces, retries, health
- `tests/test_postgres_migrations.py`: Postgres-backed migration coverage
- `tests/test_agent.py`: workflow graph, Anthropic config, and usage-tracker behavior
- `apps/web/test/api-routes.test.ts`: Next.js proxy route behavior

Notes:

- `make test` runs the Python tests only
- web tests are separate: `pnpm --dir apps/web test`
- Postgres migration tests require `AGENT_HARNESS_TEST_DATABASE_URL`

## Current Code Paths

Use these first if you want to understand the system as it exists now:

- `apps/api`
- `apps/worker`
- `apps/web`
- `packages/agent-core`
- `packages/contracts`
- `packages/observability`

## Docs Worth Reading Next

Current operational docs:

- `docs/deployment.md`
- `docs/operations.md`

Historical roadmap docs:

- `tasks/README.md`
- `tasks/01-repo-foundation.md` through `tasks/10-production-topology-and-rollout.md`

Use the `docs/` files for present-day behavior. Use the `tasks/` files to understand why the repo is shaped this way and what work remains.
