# Observability

This package now holds the shared tracing, metrics, logging, and correlation helpers used by the API and worker services.

## What It Provides

- OpenTelemetry tracer setup for each service
- Prometheus metric registration and export helpers
- Trace context propagation helpers for passing work from API to worker
- Logging correlation helpers for `run_id` and `trace_id`

## Shared Vocabulary

### Stored identifiers

- `run.trace_id`: the distributed trace id that represents the run
- `run.traceparent`: the stored W3C trace carrier used to resume the trace in workers
- `event.trace_id`: the trace id active when the event was emitted
- `event.span_id`: the span id active when the event was emitted
- `event.parent_span_id`: the parent span id for the current event span when available

### Span names

- `http.request`
- `run.execute`
- `workflow.execute`
- `node.normalize_input`
- `node.generate_response`
- `tool.normalize_whitespace`
- `model.demo-echo-model`

### Event names

- `run.created`
- `run.queued`
- `run.started`
- `run.cancel_requested`
- `run.cancelled`
- `run.completed`
- `run.failed`
- `workflow.started`
- `workflow.completed`
- `node.started`
- `node.completed`
- `tool.started`
- `tool.completed`
- `model.started`
- `model.completed`

### Metric names

- `agent_harness_http_requests_total`
- `agent_harness_http_request_duration_seconds`
- `agent_harness_runs_created_total`
- `agent_harness_runs_started_total`
- `agent_harness_run_terminal_total`
- `agent_harness_run_duration_seconds`
- `agent_harness_node_duration_seconds`
- `agent_harness_tool_calls_total`
- `agent_harness_tool_duration_seconds`
- `agent_harness_model_calls_total`
- `agent_harness_model_duration_seconds`
- `agent_harness_token_usage_total`
- `agent_harness_queue_depth`
- `agent_harness_queue_oldest_age_seconds`
- `agent_harness_active_runs`

## Local Environment

- Set `AGENT_HARNESS_OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318` to ship traces through the collector.
- The API exposes Prometheus metrics at `GET /metrics`.
- The worker serves Prometheus metrics on `AGENT_HARNESS_WORKER_METRICS_PORT` and defaults to `9101`.
