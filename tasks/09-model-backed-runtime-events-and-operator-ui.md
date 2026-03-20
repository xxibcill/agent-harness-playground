# 09 Model-Backed Runtime Events And Operator UI

Status: Done

## Goal

Preserve the current operator experience while switching the backend from demo execution to real
model-backed runs.

## Scope

- Emit structured events for model-backed workflows, including model start, model completion,
  usage, latency, and provider request identifiers when available.
- Surface workflow-specific configuration in the launcher UI using the generated contracts.
- Update the run detail view so operator-facing pages explain real model execution clearly.
- Add failure states for provider errors, configuration errors, and timeout behavior.
- Add end-to-end tests covering web submission through worker completion.

## Deliverables

- Runtime event emission for real model-backed execution.
- Launcher and run detail UI updates for structured workflow config.
- Error handling and messaging aligned with real provider failures.
- End-to-end coverage for the web-to-worker execution path.

## Exit Criteria

- An operator can start a real model-backed run from the web app and understand what happened from
  the UI alone.
- Token usage and model timing are visible in run events and stored output.
- Failure modes are actionable rather than generic.

## Notes

- Reuse the existing event model where possible so dashboards and timelines do not need a full
  redesign.
- Avoid turning provider responses into opaque blobs. Store the minimum structured data needed for
  debugging and reporting.
