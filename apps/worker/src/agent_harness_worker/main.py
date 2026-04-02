from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from agent_harness_core import (
    ExecutionCancelled,
    IsolatedExecutor,
    ResourceLimits,
    RunStore,
    RuntimeExecutor,
    build_run_store,
)
from agent_harness_core.executor import ConfigurationError, ExecutionTimedOut, ProviderError
from agent_harness_core.runtime import parse_datetime, utc_now
from agent_harness_observability import CorrelationFilter, bind_log_context, build_observability


def _env_float(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


def _env_int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


def _env_text(name: str, default: str) -> str:
    value = os.getenv(name, default).strip()
    return value or default


@dataclass(frozen=True)
class WorkerConfig:
    poll_interval_seconds: float = field(
        default_factory=lambda: _env_float("AGENT_HARNESS_WORKER_POLL_SECONDS", 1.0)
    )
    lease_seconds: int = field(
        default_factory=lambda: _env_int("AGENT_HARNESS_WORKER_LEASE_SECONDS", 30)
    )
    lease_refresh_seconds: float = field(
        default_factory=lambda: _env_float("AGENT_HARNESS_WORKER_LEASE_REFRESH_SECONDS", 10.0)
    )
    retry_backoff_seconds: int = field(
        default_factory=lambda: _env_int("AGENT_HARNESS_WORKER_RETRY_BACKOFF_SECONDS", 5)
    )
    worker_id: str = field(
        default_factory=lambda: _env_text("AGENT_HARNESS_WORKER_ID", "worker-local")
    )
    metrics_port: int = field(
        default_factory=lambda: _env_int("AGENT_HARNESS_WORKER_METRICS_PORT", 9101)
    )
    health_host: str = field(
        default_factory=lambda: _env_text("AGENT_HARNESS_WORKER_HEALTH_HOST", "127.0.0.1")
    )
    health_port: int = field(
        default_factory=lambda: _env_int("AGENT_HARNESS_WORKER_HEALTH_PORT", 9102)
    )
    health_stale_after_seconds: float = field(
        default_factory=lambda: _env_float("AGENT_HARNESS_WORKER_HEALTH_STALE_SECONDS", 45.0)
    )
    enable_process_isolation: bool = field(
        default_factory=lambda: os.getenv("AGENT_HARNESS_PROCESS_ISOLATION", "true").lower() == "true"
    )
    cpu_limit_seconds: int = field(
        default_factory=lambda: _env_int("AGENT_HARNESS_CPU_LIMIT_SECONDS", 300)
    )
    memory_limit_mb: int = field(
        default_factory=lambda: _env_int("AGENT_HARNESS_MEMORY_LIMIT_MB", 512)
    )


class WorkerHealthMonitor:
    def __init__(self, config: WorkerConfig) -> None:
        now = time.time()
        self._current_run_id: str | None = None
        self._last_finished_at: float | None = None
        self._last_finished_run_id: str | None = None
        self._last_heartbeat_at = now
        self._lock = threading.Lock()
        self._stale_after_seconds = config.health_stale_after_seconds
        self._worker_id = config.worker_id

    def mark_heartbeat(self) -> None:
        with self._lock:
            self._last_heartbeat_at = time.time()

    def mark_run_started(self, run_id: str) -> None:
        with self._lock:
            self._current_run_id = run_id
            self._last_heartbeat_at = time.time()

    def mark_run_finished(self, run_id: str) -> None:
        with self._lock:
            finished_at = time.time()
            self._current_run_id = None
            self._last_finished_at = finished_at
            self._last_finished_run_id = run_id
            self._last_heartbeat_at = finished_at

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            current_run_id = self._current_run_id
            last_finished_at = self._last_finished_at
            last_finished_run_id = self._last_finished_run_id
            last_heartbeat_at = self._last_heartbeat_at

        checked_at = time.time()
        seconds_since_heartbeat = max(0.0, checked_at - last_heartbeat_at)
        status = "ok" if seconds_since_heartbeat <= self._stale_after_seconds else "stale"
        return {
            "service": "worker",
            "status": status,
            "worker_id": self._worker_id,
            "checked_at": _format_timestamp(checked_at),
            "last_heartbeat_at": _format_timestamp(last_heartbeat_at),
            "seconds_since_heartbeat": round(seconds_since_heartbeat, 3),
            "current_run_id": current_run_id,
            "last_finished_run_id": last_finished_run_id,
            "last_finished_at": _format_timestamp(last_finished_at),
        }


def _format_timestamp(timestamp: float | None) -> str | None:
    if timestamp is None:
        return None
    return datetime.fromtimestamp(timestamp, tz=UTC).isoformat().replace("+00:00", "Z")


class WorkerHealthHttpServer(ThreadingHTTPServer):
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        request_handler_class: type[BaseHTTPRequestHandler],
        monitor: WorkerHealthMonitor,
    ) -> None:
        super().__init__(server_address, request_handler_class)
        self.monitor = monitor


class WorkerHealthRequestHandler(BaseHTTPRequestHandler):
    server: WorkerHealthHttpServer

    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/health":
            self.send_error(HTTPStatus.NOT_FOUND, "Not Found")
            return

        body = json.dumps(self.server.monitor.snapshot()).encode("utf-8")

        self.send_response(HTTPStatus.OK)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


class WorkerHealthServer:
    def __init__(self, config: WorkerConfig, monitor: WorkerHealthMonitor) -> None:
        self._server = WorkerHealthHttpServer(
            (config.health_host, config.health_port),
            WorkerHealthRequestHandler,
            monitor,
        )
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="worker-health-server",
            daemon=True,
        )

    @property
    def port(self) -> int:
        return int(self._server.server_port)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=1.0)


class LeaseHeartbeat:
    def __init__(
        self,
        store: RunStore,
        config: WorkerConfig,
        run_id: str,
        health_monitor: WorkerHealthMonitor | None = None,
    ) -> None:
        self._store = store
        self._config = config
        self._health_monitor = health_monitor
        self._run_id = run_id
        self._stop_event = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name=f"lease-heartbeat-{run_id}",
            daemon=True,
        )

    def __enter__(self) -> "LeaseHeartbeat":
        self._thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self._stop_event.set()
        self._thread.join(timeout=1.0)

    def _run(self) -> None:
        while not self._stop_event.wait(self._config.lease_refresh_seconds):
            refreshed = self._store.refresh_lease(
                self._run_id,
                self._config.worker_id,
                self._config.lease_seconds,
            )
            if refreshed is None:
                return
            if self._health_monitor is not None:
                self._health_monitor.mark_heartbeat()


def build_logger() -> logging.Logger:
    log_format = (
        "%(asctime)s %(levelname)s %(name)s "
        "run_id=%(run_id)s trace_id=%(trace_id)s %(message)s"
    )
    logger = logging.getLogger("agent_harness_worker")
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(log_format))
        handler.addFilter(CorrelationFilter())
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


def run_once(
    store: RunStore,
    executor: RuntimeExecutor,
    config: WorkerConfig,
    observability=None,
    health_monitor: WorkerHealthMonitor | None = None,
) -> bool:
    if health_monitor is not None:
        health_monitor.mark_heartbeat()

    run = store.claim_next_run(config.worker_id, config.lease_seconds)
    if run is None:
        if observability is not None:
            observability.metrics.refresh_queue(store.list_runs())
        return False

    if health_monitor is not None:
        health_monitor.mark_run_started(run.run_id)

    store.refresh_lease(run.run_id, config.worker_id, config.lease_seconds)
    try:
        with bind_log_context(run_id=run.run_id, trace_id=run.trace_id):
            with LeaseHeartbeat(store, config, run.run_id, health_monitor=health_monitor):
                executor.execute(run)
    except ExecutionCancelled as exc:
        cancelled = store.mark_run_cancelled(run.run_id, str(exc))
        if observability is not None:
            observability.metrics.record_run_terminal(cancelled)
    except ExecutionTimedOut as exc:
        _handle_retryable_failure(
            store=store,
            run=run,
            config=config,
            error=str(exc),
            observability=observability,
            event_type="run.timeout_exceeded",
        )
    except ConfigurationError as exc:
        _handle_terminal_failure(
            store=store,
            run_id=run.run_id,
            error=str(exc),
            observability=observability,
        )
    except ProviderError as exc:
        if exc.error_type in {
            "connection_error",
            "rate_limit_error",
            "service_error",
            "timeout_error",
        }:
            _handle_retryable_failure(
                store=store,
                run=run,
                config=config,
                error=str(exc),
                observability=observability,
                event_type="run.execution_failed",
            )
        else:
            _handle_terminal_failure(
                store=store,
                run_id=run.run_id,
                error=str(exc),
                observability=observability,
            )
    except Exception as exc:
        _handle_retryable_failure(
            store=store,
            run=run,
            config=config,
            error=str(exc),
            observability=observability,
            event_type="run.execution_failed",
        )
    finally:
        if health_monitor is not None:
            health_monitor.mark_run_finished(run.run_id)
    if observability is not None:
        observability.metrics.refresh_queue(store.list_runs())
    return True


def _handle_retryable_failure(
    *,
    store: RunStore,
    run,
    config: WorkerConfig,
    error: str,
    observability=None,
    event_type: str,
) -> None:
    store.append_event(
        run.run_id,
        event_type=event_type,
        category="run",
        payload={
            "error": error,
            "attempt_count": run.attempt_count,
            "max_attempts": run.max_attempts,
        },
    )
    if run.attempt_count < run.max_attempts:
        scheduled_at = _build_retry_time(run, config.retry_backoff_seconds)
        store.requeue_run(run.run_id, error, scheduled_at)
        return

    failed = store.mark_run_failed(run.run_id, error)
    if observability is not None:
        observability.metrics.record_run_terminal(failed)


def _handle_terminal_failure(
    *,
    store: RunStore,
    run_id: str,
    error: str,
    observability=None,
) -> None:
    failed = store.mark_run_failed(run_id, error)
    if observability is not None:
        observability.metrics.record_run_terminal(failed)


def _build_retry_time(run, retry_backoff_seconds: int):
    started_at = parse_datetime(run.started_at) or utc_now()
    backoff_seconds = retry_backoff_seconds * max(1, run.attempt_count)
    return max(utc_now(), started_at + timedelta(seconds=backoff_seconds))


def main() -> None:
    config = WorkerConfig()
    logger = build_logger()
    store = build_run_store()
    store.apply_migrations()
    observability = build_observability("agent-harness-worker")
    health_monitor = WorkerHealthMonitor(config)
    health_server = WorkerHealthServer(config, health_monitor)
    observability.start_metrics_server(config.metrics_port)
    health_server.start()

    # Build executor with optional process isolation
    resource_limits = None
    if config.enable_process_isolation:
        resource_limits = ResourceLimits(
            cpu_seconds=config.cpu_limit_seconds,
            memory_mb=config.memory_limit_mb,
        )
        logger.info(
            "Process isolation enabled. cpu_limit_seconds=%s memory_limit_mb=%s",
            config.cpu_limit_seconds,
            config.memory_limit_mb,
        )

    executor = IsolatedExecutor(
        store,
        observability=observability,
        resource_limits=resource_limits,
    )
    logger.info(
        "Worker started. worker_id=%s poll_interval_seconds=%.1f "
        "lease_seconds=%s metrics_port=%s health_port=%s",
        config.worker_id,
        config.poll_interval_seconds,
        config.lease_seconds,
        config.metrics_port,
        health_server.port,
    )

    try:
        while True:
            did_work = run_once(
                store,
                executor,
                config,
                observability=observability,
                health_monitor=health_monitor,
            )
            if not did_work:
                time.sleep(config.poll_interval_seconds)
    finally:
        health_server.stop()
        observability.shutdown()


if __name__ == "__main__":
    main()
