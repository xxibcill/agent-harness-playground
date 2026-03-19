# 02 Backend Runtime

Status: completed

## Goal

Turn the current prototype into a backend platform that can accept, schedule, execute, and inspect agent runs.

## Scope

- Build `apps/api` with endpoints for create run, list runs, get run, cancel run, and stream run events.
- Build `apps/worker` to execute LangGraph workflows asynchronously.
- Define durable run models in Postgres.
- Define structured runtime events for workflow, node, tool, and model activity.
- Store run outputs, errors, and token usage in queryable tables.

## Deliverables

- Durable `run_id` lifecycle from request creation to completion.
- Queue-backed worker execution.
- A stable event schema for live updates and historical inspection.
- Database migrations for core runtime tables.

## Exit Criteria

- A user can trigger a run through the API and inspect the full result later.
- A worker can recover and resume processing after restart.
- The system records enough state for the future frontend to render run history and live progress.

## Notes

- Treat the API as the control plane.
- Treat the worker as the execution plane.
- Do not let the UI depend on raw backend logs for state reconstruction.

## Implemented In This Step

- Added a durable backend runtime contract package for run records, lifecycle states, and structured runtime events.
- Added a shared runtime core with Postgres migrations, run persistence, event storage, lease-based queue claiming, and a queryable usage table.
- Implemented `apps/api` endpoints for create, list, get, cancel, and streaming run events with a testable in-memory store option.
- Implemented `apps/worker` execution with a LangGraph-backed demo workflow that emits workflow, node, tool, and model events.
- Added lifecycle tests covering API behavior, worker execution, cancellation, and event history replay.
