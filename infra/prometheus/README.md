# Prometheus

This directory now holds local scrape and alert configuration for:

- API metrics at `http://api:8000/metrics`
- worker metrics at `http://worker:9101/metrics`
- collector metrics at `http://otel-collector:8889/metrics`

`prometheus.yml` wires the scrape jobs and loads `alerts.yml` with the initial alerting plan for failures, stuck runs, and degraded throughput.
