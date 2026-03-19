# 04 Next.js Client

## Goal

Build the frontend as a pure client application for starting runs and monitoring agent execution.

## Scope

- Build `apps/web` in Next.js.
- Add a run creation flow with prompt, workflow, and configuration inputs.
- Add a live run page that consumes backend streaming updates.
- Add graphical workflow rendering for node states and transitions.
- Add operator dashboards for metrics, recent runs, and failure inspection.
- Add trace and event timeline views for debugging.

## Deliverables

- Run launcher UI.
- Live monitoring UI with graph, timeline, and metrics panels.
- Historical run detail pages.
- Shared frontend types generated from backend contracts.

## Exit Criteria

- A user can start a run from the browser.
- The UI updates during execution without page refresh.
- A completed run can be inspected later with enough detail to explain what happened.

## Notes

- Even though Next.js can host server routes, the agent runtime should stay in Python services.
- Use streaming updates from the backend rather than polling-only status checks.
- Optimize the UI for operator clarity first, then visual polish.
