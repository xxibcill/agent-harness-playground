# Web

This application is the future Next.js monitoring client for triggering runs and visualizing backend execution.

Environment:

- `AGENT_HARNESS_API_BASE_URL`: server-side FastAPI base URL
- `AGENT_HARNESS_API_TOKEN`: server-side API token used by the Next.js proxy
- `AGENT_HARNESS_WEB_TRUSTED_PROXY_SECRET`: enables trusted proxy mode for multi-user deployments
- `AGENT_HARNESS_WEB_TRUSTED_PROXY_SECRET_HEADER`: optional override for the proxy secret header name
- `AGENT_HARNESS_WEB_TRUSTED_PROXY_USER_HEADER`: optional override for the forwarded user header name
- `AGENT_HARNESS_WEB_TRUSTED_PROXY_ROLE_HEADER`: optional override for the forwarded role header name
- `AGENT_HARNESS_WEB_DEV_ROLE`: optional local-development fallback role when trusted proxy mode is off

The browser now calls same-origin `/api/runs` routes. Next.js proxies those requests to the FastAPI
service with server-side credentials, and trusted proxy mode can enforce an upstream operator
identity boundary without exposing raw backend tokens to the browser.
