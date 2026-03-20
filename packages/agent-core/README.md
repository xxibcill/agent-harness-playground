# Agent Core

This package now holds the shared backend runtime core:

- Postgres-backed run storage and migrations
- in-memory test store for API and worker tests
- a LangGraph-backed demo workflow executor
- worker-safe leasing and event persistence helpers
- trace-aware run and event persistence for API-to-worker correlation
- metrics hooks for run, node, tool, and model execution

The original runnable prototype still remains in `src/basic_langgraph_agent/` at the repository root.
