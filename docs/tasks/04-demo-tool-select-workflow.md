# 04 Demo Tool Select Workflow

Status: Done

## Goal

Add a workflow that teaches tool selection while still avoiding loops and model providers.

## Scope

- Create a workflow such as `demo.tool.select`.
- Select among a few local deterministic tools such as calculator, capital lookup, and word count.
- Keep the maximum number of tool calls at one.
- Return a final answer that explains which tool was chosen.

## Deliverables

- A workflow module that chooses one local tool based on input shape.
- Registry integration.
- Input examples that map cleanly to each tool path.

## Exit Criteria

- The workflow can choose the right tool for supported prompt types.
- The workflow never loops back for another reasoning step.
- Learners can compare this workflow against `demo.tool.single` and see only one new concept:
  selection.

## Notes

- Reuse existing tool helpers where practical.
- If tool selection is ambiguous, prefer a simple fallback response over hidden heuristics.
