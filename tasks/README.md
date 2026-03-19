# Task Breakdown

This folder breaks the long-term monorepo plan into small milestones that can be delivered one at a time.

## Suggested Order

1. `01-repo-foundation.md` - completed
2. `02-backend-runtime.md` - next
3. `03-observability.md`
4. `04-nextjs-client.md`
5. `05-hardening-and-release.md`

## Delivery Rule

- Each milestone should leave the repository in a stable, runnable state.
- Avoid large cross-cutting migrations in a single step.
- Keep the current Python prototype working until its replacement is production-ready.
- Prefer generated contracts and shared schemas over duplicated types across Python and TypeScript.
