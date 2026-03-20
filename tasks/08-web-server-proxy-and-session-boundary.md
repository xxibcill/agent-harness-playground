# 08 Web Server Proxy And Session Boundary

Status: Done

## Goal

Remove the direct browser-to-API trust model and make the web app the single entry point for
operators.

## Scope

- Add Next.js server routes that proxy run creation, listing, detail fetches, cancellation, and
  event streaming to the FastAPI service.
- Move API credentials to the server side so the browser no longer needs a public operator token.
- Update the client-side data layer to call same-origin web routes instead of the backend service
  directly.
- Establish the session or trusted proxy boundary needed for multi-user deployments.
- Add tests for proxy behavior, auth forwarding, and streaming pass-through.

## Deliverables

- Server-side API proxy routes in `apps/web`.
- Client API helpers that call same-origin routes.
- Removal of the browser-exposed token requirement for normal usage.
- Coverage for authenticated proxy calls and SSE forwarding.

## Exit Criteria

- The browser can launch and monitor runs without a direct backend token.
- The web app can be deployed for more than one trusted operator without exposing raw API
  credentials client-side.
- Streaming updates still work through the web layer.

## Notes

- Keep Python services responsible for orchestration. The web layer should act as a boundary and
  proxy, not a second runtime.
- Prefer a design that can later plug into real user sessions without another large rewrite.
