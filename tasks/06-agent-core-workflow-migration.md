# 06 Agent Core Workflow Migration

Status: Done

## Goal

Move the real model-backed agent out of the prototype CLI package and into `packages/agent-core`
so the worker can execute it as a first-class workflow.

## Scope

- Extract the reusable Anthropic-compatible client setup, configuration loading, and LangGraph
  builder from `src/basic_langgraph_agent/`.
- Introduce a workflow registry in `packages/agent-core` so the worker can dispatch by workflow
  name instead of using a single hardcoded graph.
- Keep `demo.echo` working while adding a new model-backed workflow such as
  `anthropic.respond`.
- Reduce the prototype CLI to a thin wrapper around the shared runtime code.
- Add tests for workflow lookup, configuration errors, and successful worker execution.

## Deliverables

- Shared workflow modules in `packages/agent-core`.
- Registry-based execution path in the runtime executor.
- A thin CLI compatibility layer for local terminal usage.
- Automated tests that cover the new registry and the migrated model-backed workflow.

## Exit Criteria

- A queued run can select either `demo.echo` or the new model-backed workflow.
- The worker no longer depends on prototype-only code paths to execute real agent logic.
- The repository still supports local CLI execution for the migrated workflow.

## Notes

- Do not remove the prototype entry point until the new shared workflow path is fully covered by
  tests.
- Keep configuration loading explicit and fail fast when model credentials or required settings
  are missing.
