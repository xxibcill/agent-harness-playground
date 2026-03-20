# OpenTelemetry Collector

This directory now holds the local collector and Tempo configuration used by the observability milestone.

- `collector.yaml` receives OTLP traces from the API and worker, exports them to Tempo, and exposes collector metrics to Prometheus.
- `tempo.yaml` provisions the local Tempo instance used as the trace backend.
