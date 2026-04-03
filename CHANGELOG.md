# Changelog

All notable changes to this repository will be documented in this file.

This changelog was initialized from the milestone documents in [`tasks/`](/Users/jjae/Documents/guthib/agent-harness-playground/tasks). Early history is grouped by milestone instead of release tag because no formal version history has been recorded yet.

## Unreleased

### Planned

- Educational workflow learning ladder: incremental demos teaching routing, tool use, tool selection, one-shot reasoning, and looping agent behavior.
- Deterministic demo workflows: `demo.route`, `demo.tool.single`, `demo.tool.select`, `demo.react.once`.
- Refined `demo.react` documentation to clearly present looping agent behavior.
- Dashboard workflow catalog showing the learning progression in the operator UI.
- README workflow lessons section with recommended run order and example prompts.
- Automated tests locking down the educational workflow progression.
- Optional Anthropic React capstone combining provider reasoning with tool use.

### Removed

- Deleted the legacy root CLI prototype and its console script entrypoints.

### Changed

- Retargeted root package metadata, docs, and tests to the current `agent-core` workflow surface.

## Milestone 10 - Production Topology and Rollout

### Added

- Deployment documentation for the unified web entry point topology.
- Runtime configuration guidance for private backend services and workers.
- Canary procedure verifying a run can be launched from the web app and completed by a worker.
- Operations notes for auth, secrets, monitoring, and rollback.

### Changed

- Hardened the integrated path from milestones 06-09 without introducing new architectural changes.
- Kept the deployment model simple enough for local development to remain practical.

## Milestone 09 - Model-Backed Runtime Events and Operator UI

### Added

- Runtime event emission for real model-backed execution including model start, completion, usage, latency, and provider request identifiers.
- Launcher and run detail UI updates for structured workflow configuration.
- Error handling and messaging aligned with real provider failures, configuration errors, and timeout behavior.
- End-to-end tests covering web submission through worker completion.

### Changed

- Preserved the existing operator experience while switching from demo execution to real model-backed runs.

## Milestone 08 - Web Server Proxy and Session Boundary

### Added

- Next.js server routes that proxy run creation, listing, detail fetches, cancellation, and event streaming to the FastAPI service.
- Client API helpers that call same-origin routes.
- Coverage for authenticated proxy calls and SSE forwarding.

### Changed

- Moved API credentials to the server side so the browser no longer needs a public operator token.
- Updated the client-side data layer to call same-origin web routes instead of the backend directly.

### Removed

- Browser-exposed token requirement for normal operator usage.

## Milestone 07 - Run Contracts and Generated Types

### Added

- Explicit workflow configuration object in the shared contracts.
- Regenerated TypeScript types from the Python source of truth.
- Test coverage for defaulting, validation, and backward compatibility.

### Changed

- Replaced free-form `metadata` usage with structured workflow configuration for real execution.

## Milestone 06 - Agent Core Workflow Migration

### Added

- Shared workflow modules in `packages/agent-core` with registry-based execution.
- Thin CLI compatibility layer for local terminal usage.
- Tests covering workflow lookup, configuration errors, and worker execution.

### Changed

- Extracted reusable Anthropic-compatible client setup and LangGraph builder from the prototype CLI into the shared runtime.
- Worker now dispatches by workflow name instead of using a single hardcoded graph.

## Milestone 05 - Hardening and Release

### Added

- API token authentication with `viewer`, `operator`, and `admin` roles for the FastAPI control plane.
- Runtime policy support for per-run retry and timeout defaults persisted in the database.
- CI validation for Python linting, type checks, frontend type checks, tests, and Postgres-backed migration safety.
- Deployment and operations guides covering release order, rollback, retention, backups, and incident playbooks.

### Changed

- Established production-minded runtime defaults for attempts, timeouts, lease heartbeats, and retry backoff.
- Restricted `/metrics` and API docs to admin access while keeping run reads and run mutations role-gated.

## Milestone 04 - Next.js Client

### Added

- Next.js operator console for launching runs and monitoring execution from the browser.
- Dashboard and run detail views for recent runs, live progress, and execution history.
- Frontend API helpers and generated contract types for the web client.

### Changed

- Kept orchestration in Python services while treating the web app as a pure client surface.
- Preferred backend event streams for live updates instead of reconstructing state from logs.

## Milestone 03 - Observability

### Added

- Shared observability package for OpenTelemetry tracing, Prometheus metrics, and log correlation.
- Trace correlation fields on stored runs and events so API requests and worker execution share a distributed trace.
- Metrics for HTTP traffic, run throughput, latency, failures, token usage, and queue health.
- Local collector, Tempo, Prometheus, Grafana dashboards, and alert rules for runtime visibility.

### Changed

- Aligned event names with tracing and metric vocabulary across services.
- Made run execution observable both in real time and through durable historical records.

## Milestone 02 - Backend Runtime

### Added

- Durable backend contracts for run records, lifecycle states, structured runtime events, and token usage.
- Shared runtime core with Postgres migrations, persistence, event storage, queue claiming, and usage tracking.
- FastAPI endpoints to create, list, inspect, cancel, and stream run events.
- Worker execution service for asynchronous LangGraph runs with workflow, node, tool, and model event emission.
- Lifecycle and persistence tests covering API behavior, worker execution, cancellation, and event replay.

### Changed

- Reframed the API as the control plane and the worker as the execution plane.
- Moved the platform from a prototype-only flow toward durable, queryable run history.

## Milestone 01 - Repo Foundation

### Added

- Monorepo structure with `apps/`, `packages/`, `infra/`, and `tasks/`.
- Initial service scaffolds for `apps/api`, `apps/worker`, and `apps/web`.
- Shared package boundaries for runtime core, observability, and contracts.
- Root workspace tooling and local infrastructure for Postgres and Redis.
- Task documents describing the planned implementation order.

### Changed

- Preserved the original Python LangGraph prototype while introducing the new repository layout.
- Clarified ownership boundaries between frontend, API, worker, and shared packages.
