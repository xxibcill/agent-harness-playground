# 01 Repo Foundation

Status: completed

## Goal

Prepare the repository for a mixed Python and TypeScript monorepo without breaking the current prototype.

## Scope

- Create the top-level `apps/`, `packages/`, `infra/`, and `tasks/` directories.
- Choose and wire workspace tooling for both ecosystems.
- Scaffold `apps/web` as a Next.js app.
- Scaffold `apps/api` and `apps/worker` as Python services.
- Move reusable agent logic toward `packages/agent-core`.
- Add shared developer scripts for install, lint, test, and local startup.

## Deliverables

- Workspace layout committed and documented.
- Basic local development setup for Python and Node.js.
- Initial container setup for Postgres and Redis.
- Clear ownership boundaries between frontend, API, worker, and shared packages.

## Exit Criteria

- A developer can install dependencies and start placeholder services locally.
- The current LangGraph prototype still runs.
- The repo structure matches the architecture described in the root README.

## Main Decisions

- Use Next.js for the frontend.
- Keep agent orchestration in Python.
- Keep frontend and backend in the same repository, but with explicit boundaries.

## Implemented In This Step

- Added `apps/api` as the future FastAPI control plane.
- Added `apps/worker` as the future background execution service.
- Added `apps/web` as a minimal Next.js monitoring client shell.
- Added `packages/agent-core`, `packages/observability`, and `packages/contracts`.
- Added root workspace and developer tooling files.
- Added initial Postgres and Redis local infrastructure configuration.
- Verified the existing Python prototype still passes its test suite.
