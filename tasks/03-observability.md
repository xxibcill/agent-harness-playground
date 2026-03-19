# 03 Observability

## Goal

Add tracing, monitoring, and runtime visibility so agent execution can be understood in real time and after the fact.

## Scope

- Instrument API and worker services with OpenTelemetry.
- Create spans for runs, graph nodes, tool calls, model calls, and external integrations.
- Export metrics for throughput, latency, failures, token usage, and queue health.
- Ship traces to a trace backend and metrics to Prometheus.
- Provision Grafana dashboards for operators and developers.
- Correlate logs, traces, and stored events with shared identifiers.

## Deliverables

- Trace propagation from API request to worker execution.
- Dashboards for run volume, success rate, latency, and token consumption.
- Alerting plan for failures, stuck runs, and degraded throughput.
- Documented event and tracing vocabulary.

## Exit Criteria

- Every run has a visible trace.
- Every failure can be correlated with a run record and a trace.
- Operations data is good enough for the planned graphical monitoring UI.

## Notes

- Prefer explicit spans and metrics over generic log scraping.
- Keep naming consistent across code, traces, metrics, and dashboards.
