# 03 Demo Tool Single Workflow

Status: Done

## Goal

Add a workflow that always performs one local tool call so learners can isolate tool execution from
tool selection.

## Scope

- Create a workflow such as `demo.tool.single`.
- Reuse one deterministic local tool, for example word counting.
- Keep the control flow linear: normalize, call tool, respond.
- Register the workflow in the shared registry.

## Deliverables

- A workflow module with one fixed tool path.
- Clear response text that makes the tool input and output visible.
- Basic examples for prompts that demonstrate the workflow.

## Exit Criteria

- A run always executes one tool call before responding.
- No planner or branching logic is required to choose the tool.
- The event stream makes the single tool step obvious to a learner.

## Notes

- This task should teach "what a tool call is", not "how to choose among tools".
- Prefer simple inputs that are easy to verify by eye.
