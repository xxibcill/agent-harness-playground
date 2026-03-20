from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterator

from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.propagate import inject
from opentelemetry.sdk.resources import SERVICE_NAME, SERVICE_VERSION, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor, SpanExporter
from opentelemetry.trace import SpanKind, Tracer
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, make_asgi_app, start_http_server

from agent_harness_contracts import RunRecord, RunStatus, TokenUsage

_trace_id_var: ContextVar[str] = ContextVar("agent_harness_trace_id", default="-")
_run_id_var: ContextVar[str] = ContextVar("agent_harness_run_id", default="-")


def _parse_timestamp(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value)


def _seconds_between(start: str | None, end: str | None) -> float | None:
    started_at = _parse_timestamp(start)
    ended_at = _parse_timestamp(end)
    if started_at is None or ended_at is None:
        return None
    return max((ended_at - started_at).total_seconds(), 0.0)


def _current_utc() -> datetime:
    return datetime.now(timezone.utc)


def _status_value(status: RunStatus | str) -> str:
    if isinstance(status, RunStatus):
        return status.value
    return status


def format_trace_id(trace_id: int) -> str | None:
    if trace_id == 0:
        return None
    return f"{trace_id:032x}"


def format_span_id(span_id: int) -> str | None:
    if span_id == 0:
        return None
    return f"{span_id:016x}"


@dataclass(frozen=True)
class TraceSnapshot:
    trace_id: str | None
    span_id: str | None
    parent_span_id: str | None
    traceparent: str | None


def capture_current_trace() -> TraceSnapshot:
    span = trace.get_current_span()
    span_context = span.get_span_context()
    if not span_context.is_valid:
        return TraceSnapshot(None, None, None, None)

    carrier: dict[str, str] = {}
    inject(carrier)
    parent = getattr(span, "parent", None)
    parent_span_id = None
    if parent is not None and getattr(parent, "is_valid", False):
        parent_span_id = format_span_id(parent.span_id)
    return TraceSnapshot(
        trace_id=format_trace_id(span_context.trace_id),
        span_id=format_span_id(span_context.span_id),
        parent_span_id=parent_span_id,
        traceparent=carrier.get("traceparent"),
    )


def context_from_traceparent(traceparent: str | None) -> Any:
    if not traceparent:
        return otel_context.get_current()
    return TraceContextTextMapPropagator().extract({"traceparent": traceparent})


@contextmanager
def bind_log_context(*, run_id: str | None = None, trace_id: str | None = None) -> Iterator[None]:
    tokens: list[tuple[ContextVar[str], Token[str]]] = []
    if run_id is not None:
        tokens.append((_run_id_var, _run_id_var.set(run_id)))
    if trace_id is not None:
        tokens.append((_trace_id_var, _trace_id_var.set(trace_id)))
    try:
        yield
    finally:
        for variable, token in reversed(tokens):
            variable.reset(token)


class CorrelationFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.run_id = _run_id_var.get()
        record.trace_id = _trace_id_var.get()
        return True


class PrometheusMetrics:
    def __init__(self, service_name: str, registry: CollectorRegistry | None = None) -> None:
        self.registry = registry or CollectorRegistry()
        namespace = "agent_harness"

        self.http_requests_total = Counter(
            "http_requests_total",
            "Total HTTP requests handled by the service.",
            ("service", "method", "route", "status_code"),
            namespace=namespace,
            registry=self.registry,
        )
        self.http_request_duration_seconds = Histogram(
            "http_request_duration_seconds",
            "HTTP request latency by route.",
            ("service", "method", "route"),
            namespace=namespace,
            registry=self.registry,
        )
        self.run_created_total = Counter(
            "runs_created_total",
            "Runs created through the control plane.",
            ("workflow",),
            namespace=namespace,
            registry=self.registry,
        )
        self.run_started_total = Counter(
            "runs_started_total",
            "Runs claimed and started by workers.",
            ("workflow", "worker_id"),
            namespace=namespace,
            registry=self.registry,
        )
        self.run_terminal_total = Counter(
            "run_terminal_total",
            "Runs that reached a terminal state.",
            ("workflow", "status"),
            namespace=namespace,
            registry=self.registry,
        )
        self.run_duration_seconds = Histogram(
            "run_duration_seconds",
            "End-to-end run duration in seconds.",
            ("workflow", "status"),
            namespace=namespace,
            registry=self.registry,
        )
        self.node_duration_seconds = Histogram(
            "node_duration_seconds",
            "Workflow node duration in seconds.",
            ("workflow", "node_name", "outcome"),
            namespace=namespace,
            registry=self.registry,
        )
        self.tool_calls_total = Counter(
            "tool_calls_total",
            "Tool calls emitted by workflows.",
            ("workflow", "node_name", "tool_name", "outcome"),
            namespace=namespace,
            registry=self.registry,
        )
        self.tool_duration_seconds = Histogram(
            "tool_duration_seconds",
            "Tool call duration in seconds.",
            ("workflow", "node_name", "tool_name", "outcome"),
            namespace=namespace,
            registry=self.registry,
        )
        self.model_calls_total = Counter(
            "model_calls_total",
            "Model calls emitted by workflows.",
            ("workflow", "node_name", "model_name", "outcome"),
            namespace=namespace,
            registry=self.registry,
        )
        self.model_duration_seconds = Histogram(
            "model_duration_seconds",
            "Model call duration in seconds.",
            ("workflow", "node_name", "model_name", "outcome"),
            namespace=namespace,
            registry=self.registry,
        )
        self.token_usage_total = Counter(
            "token_usage_total",
            "Aggregated token usage by workflow and model.",
            ("workflow", "model_name", "direction"),
            namespace=namespace,
            registry=self.registry,
        )
        self.queue_depth = Gauge(
            "queue_depth",
            "Queued runs waiting for execution.",
            ("service",),
            namespace=namespace,
            registry=self.registry,
        )
        self.queue_oldest_age_seconds = Gauge(
            "queue_oldest_age_seconds",
            "Age of the oldest queued run in seconds.",
            ("service",),
            namespace=namespace,
            registry=self.registry,
        )
        self.active_runs = Gauge(
            "active_runs",
            "Runs currently executing or cancelling.",
            ("service",),
            namespace=namespace,
            registry=self.registry,
        )
        self.service_name = service_name

    def record_http_request(
        self,
        *,
        method: str,
        route: str,
        status_code: int,
        duration_seconds: float,
    ) -> None:
        labels = {
            "service": self.service_name,
            "method": method,
            "route": route,
        }
        self.http_requests_total.labels(
            **labels,
            status_code=str(status_code),
        ).inc()
        self.http_request_duration_seconds.labels(**labels).observe(duration_seconds)

    def record_run_created(self, run: RunRecord) -> None:
        self.run_created_total.labels(workflow=run.workflow).inc()

    def record_run_started(self, run: RunRecord) -> None:
        worker_id = run.worker_id or "unknown"
        self.run_started_total.labels(workflow=run.workflow, worker_id=worker_id).inc()

    def record_run_terminal(self, run: RunRecord) -> None:
        status = _status_value(run.status)
        self.run_terminal_total.labels(workflow=run.workflow, status=status).inc()
        duration_seconds = _seconds_between(run.started_at or run.created_at, run.completed_at)
        if duration_seconds is not None:
            self.run_duration_seconds.labels(workflow=run.workflow, status=status).observe(duration_seconds)

    def record_node(self, *, workflow: str, node_name: str, outcome: str, duration_seconds: float) -> None:
        self.node_duration_seconds.labels(
            workflow=workflow,
            node_name=node_name,
            outcome=outcome,
        ).observe(duration_seconds)

    def record_tool(
        self,
        *,
        workflow: str,
        node_name: str,
        tool_name: str,
        outcome: str,
        duration_seconds: float,
    ) -> None:
        labels = {
            "workflow": workflow,
            "node_name": node_name,
            "tool_name": tool_name,
            "outcome": outcome,
        }
        self.tool_calls_total.labels(**labels).inc()
        self.tool_duration_seconds.labels(**labels).observe(duration_seconds)

    def record_model(
        self,
        *,
        workflow: str,
        node_name: str,
        model_name: str,
        outcome: str,
        duration_seconds: float,
        usage: TokenUsage | None = None,
    ) -> None:
        labels = {
            "workflow": workflow,
            "node_name": node_name,
            "model_name": model_name,
            "outcome": outcome,
        }
        self.model_calls_total.labels(**labels).inc()
        self.model_duration_seconds.labels(**labels).observe(duration_seconds)
        if usage is None:
            return
        self.token_usage_total.labels(
            workflow=workflow,
            model_name=model_name,
            direction="input",
        ).inc(usage.input_tokens)
        self.token_usage_total.labels(
            workflow=workflow,
            model_name=model_name,
            direction="output",
        ).inc(usage.output_tokens)
        self.token_usage_total.labels(
            workflow=workflow,
            model_name=model_name,
            direction="total",
        ).inc(usage.total_tokens)

    def refresh_queue(self, runs: list[RunRecord]) -> None:
        queued = [run for run in runs if _status_value(run.status) == RunStatus.QUEUED.value]
        active = [
            run
            for run in runs
            if _status_value(run.status) in {RunStatus.RUNNING.value, RunStatus.CANCELLING.value}
        ]
        now = _current_utc()
        self.queue_depth.labels(service=self.service_name).set(len(queued))
        self.active_runs.labels(service=self.service_name).set(len(active))
        if not queued:
            self.queue_oldest_age_seconds.labels(service=self.service_name).set(0)
            return
        oldest = min(datetime.fromisoformat(run.created_at) for run in queued)
        self.queue_oldest_age_seconds.labels(service=self.service_name).set(
            max((now - oldest).total_seconds(), 0.0)
        )


class ServiceObservability:
    def __init__(
        self,
        service_name: str,
        *,
        service_version: str = "0.1.0",
        metrics_registry: CollectorRegistry | None = None,
        span_exporter: SpanExporter | None = None,
        use_batch_processor: bool = True,
    ) -> None:
        self.service_name = service_name
        self.metrics = PrometheusMetrics(service_name, registry=metrics_registry)
        self._tracer_provider = TracerProvider(
            resource=Resource.create(
                {
                    SERVICE_NAME: service_name,
                    SERVICE_VERSION: service_version,
                }
            )
        )
        exporter = span_exporter or _build_otlp_exporter()
        if exporter is not None:
            processor_cls = BatchSpanProcessor if use_batch_processor else SimpleSpanProcessor
            self._tracer_provider.add_span_processor(processor_cls(exporter))
        self.tracer = self._tracer_provider.get_tracer("agent_harness", service_version)

    def metrics_app(self) -> Any:
        return make_asgi_app(registry=self.metrics.registry)

    def start_metrics_server(self, port: int) -> None:
        start_http_server(port, registry=self.metrics.registry)

    def shutdown(self) -> None:
        self._tracer_provider.force_flush()
        self._tracer_provider.shutdown()


def _build_otlp_exporter() -> SpanExporter | None:
    endpoint = os.getenv("AGENT_HARNESS_OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    if not endpoint:
        return None
    return OTLPSpanExporter(endpoint=f"{endpoint.rstrip('/')}/v1/traces")


def build_observability(
    service_name: str,
    *,
    service_version: str = "0.1.0",
    metrics_registry: CollectorRegistry | None = None,
    span_exporter: SpanExporter | None = None,
    use_batch_processor: bool = True,
) -> ServiceObservability:
    return ServiceObservability(
        service_name,
        service_version=service_version,
        metrics_registry=metrics_registry,
        span_exporter=span_exporter,
        use_batch_processor=use_batch_processor,
    )


@contextmanager
def start_span(
    tracer: Tracer,
    name: str,
    *,
    kind: SpanKind = SpanKind.INTERNAL,
    context: Any | None = None,
    attributes: dict[str, Any] | None = None,
) -> Iterator[Any]:
    with tracer.start_as_current_span(
        name,
        context=context,
        kind=kind,
        attributes=attributes,
    ) as span:
        yield span
