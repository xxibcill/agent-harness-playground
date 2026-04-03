# 02 Demo Route Workflow

Status: Done

## Goal

Add a deterministic workflow that introduces branching without introducing tool use.

## Scope

- Create a new workflow such as `demo.route`.
- Normalize input and classify it into a small number of fixed categories.
- Return a canned response based on the selected route.
- Register the workflow in the shared registry.

## Deliverables

- A new workflow module in `packages/agent-core/src/agent_harness_core/workflows/`.
- Registry entry so the worker can execute the new workflow.
- Minimal response rules and examples suitable for teaching routing behavior.

## Exit Criteria

- A run can execute `demo.route` from the normal runtime path.
- The workflow shows branching in events and final output, without any tool calls.
- The behavior is deterministic for the same input.

## Notes

- Keep the category set small and easy to understand.
- Avoid provider calls, randomness, or looping behavior.
