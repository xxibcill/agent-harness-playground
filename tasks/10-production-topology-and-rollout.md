# 10 Production Topology And Rollout

Status: Done

## Goal

Deploy the web app as the public surface, keep execution services private, and define a rollout
path that supports shared production usage.

## Scope

- Document and implement the target topology for web, API, worker, database, and observability
  services.
- Standardize environment variables and secret handling for the web proxy layer and model-backed
  worker execution.
- Define deployment order, health checks, canary validation, and rollback steps for the seamless
  web-driven flow.
- Add production checks for same-origin routing, worker liveness, and end-to-end run completion.
- Update operations documentation for shared usage, incident response, and credential rotation.

## Deliverables

- Deployment documentation for the unified web entry point.
- Runtime configuration guidance for private backend services and workers.
- Canary procedure that verifies a run can be launched from the web app and completed by a worker.
- Operations notes for auth, secrets, monitoring, and rollback.

## Exit Criteria

- The recommended deployment no longer requires exposing backend operator tokens to browsers.
- A fresh environment can be deployed and validated with a web-triggered canary run.
- Operational ownership is clear for the web surface, backend runtime, and worker fleet.

## Notes

- This milestone should harden the integrated path created by Tasks 06 through 09 rather than
  introducing another large architectural change.
- Keep the deployment model simple enough for local development to remain practical.
