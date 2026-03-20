# Web

This application is the future Next.js monitoring client for triggering runs and visualizing backend execution.

Environment:

- `NEXT_PUBLIC_API_BASE_URL`: browser-visible API base URL
- `NEXT_PUBLIC_API_TOKEN`: scoped operator token for direct browser access

The current UI calls the API directly from the browser. That is acceptable for local or single-operator usage. Shared production deployments should move these requests behind trusted server-side routes or another authenticated proxy.
