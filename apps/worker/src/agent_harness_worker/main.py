from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from datetime import timedelta

from agent_harness_core import (
    ExecutionCancelled,
    RunStore,
    RuntimeExecutor,
    build_run_store,
)
from agent_harness_core.executor import ConfigurationError, ExecutionTimedOut, ProviderError
from agent_harness_core.runtime import parse_datetime, utc_now
from agent_harness_observability import CorrelationFilter, bind_log_context, build_observability


@dataclass(frozen=True)
class WorkerConfig:
    poll_interval_seconds: float = float(os.getenv("AGENT_HARNESS_WORKER_POLL_SECONDS", "1.0"))
    lease_seconds: int = int(os.getenv("AGENT_HARNESS_WORKER_LEASE_SECONDS", "30"))
    lease_refresh_seconds: float = float(
        os.getenv("AGENT_HARNESS_WORKER_LEASE_REFRESH_SECONDS", "10.0")
    )
    retry_backoff_seconds: int = int(os.getenv("AGENT_HARNESS_WORKER_RETRY_BACKOFF_SECONDS", "5"))
    worker_id: str = os.getenv("AGENT_HARNESS_WORKER_ID", "worker-local")
    metrics_port: int = int(os.getenv("AGENT_HARNESS_WORKER_METRICS_PORT", "9101"))


class LeaseHeartbeat:
    def __init__(self, store: RunStore, config: WorkerConfig, run_id: str) -> None:
        self._store = store
        self._config = config
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
) -> bool:
    run = store.claim_next_run(config.worker_id, config.lease_seconds)
    if run is None:
        if observability is not None:
            observability.metrics.refresh_queue(store.list_runs())
        return False

    store.refresh_lease(run.run_id, config.worker_id, config.lease_seconds)
    try:
        with bind_log_context(run_id=run.run_id, trace_id=run.trace_id):
            with LeaseHeartbeat(store, config, run.run_id):
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
    observability.start_metrics_server(config.metrics_port)
    executor = RuntimeExecutor(store, observability=observability)
    logger.info(
        "Worker started. worker_id=%s poll_interval_seconds=%.1f lease_seconds=%s metrics_port=%s",
        config.worker_id,
        config.poll_interval_seconds,
        config.lease_seconds,
        config.metrics_port,
    )

    try:
        while True:
            did_work = run_once(store, executor, config, observability=observability)
            if not did_work:
                time.sleep(config.poll_interval_seconds)
    finally:
        observability.shutdown()


if __name__ == "__main__":
    main()
