# Task Breakdown

This folder breaks the long-term monorepo plan into small milestones that can be delivered one at a time.

## Suggested Order

1. `01-repo-foundation.md` - completed
2. `02-backend-runtime.md` - next
3. `03-observability.md`
4. `04-nextjs-client.md`
5. `05-hardening-and-release.md`
6. `06-agent-core-workflow-migration.md`
7. `07-run-contracts-and-generated-types.md`
8. `08-web-server-proxy-and-session-boundary.md`
9. `09-model-backed-runtime-events-and-operator-ui.md`
10. `10-production-topology-and-rollout.md`

## Next Phase

The tasks above cover the current monorepo foundation. The tasks below break the remaining
"seamless web app" work into medium-sized milestones:

- move the real model-backed agent into the shared Python runtime
- make workflow selection and execution config explicit in shared contracts
- remove direct browser-to-API trust assumptions
- preserve rich runtime events while switching from demo execution to real model calls
- deploy the system as a single operator-facing web surface with private backend workers

## Delivery Rule

- Each milestone should leave the repository in a stable, runnable state.
- Avoid large cross-cutting migrations in a single step.
- Keep the current Python prototype working until its replacement is production-ready.
- Prefer generated contracts and shared schemas over duplicated types across Python and TypeScript.
