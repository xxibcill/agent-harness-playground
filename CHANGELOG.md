# Changelog

All notable changes to this repository will be documented in this file.

This changelog was initialized from the milestone documents in [`tasks/`](/Users/jjae/Documents/guthib/agent-harness-playground/tasks). Early history is grouped by milestone instead of release tag because no formal version history has been recorded yet.

## Unreleased

- No unreleased changes documented yet.

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
