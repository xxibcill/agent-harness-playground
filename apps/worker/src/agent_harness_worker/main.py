from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass

from agent_harness_core import ExecutionCancelled, RuntimeExecutor, RunStore, build_run_store
from agent_harness_observability import CorrelationFilter, bind_log_context, build_observability


@dataclass(frozen=True)
class WorkerConfig:
    poll_interval_seconds: float = float(os.getenv("AGENT_HARNESS_WORKER_POLL_SECONDS", "1.0"))
    lease_seconds: int = int(os.getenv("AGENT_HARNESS_WORKER_LEASE_SECONDS", "30"))
    worker_id: str = os.getenv("AGENT_HARNESS_WORKER_ID", "worker-local")
    metrics_port: int = int(os.getenv("AGENT_HARNESS_WORKER_METRICS_PORT", "9101"))


def build_logger() -> logging.Logger:
    logger = logging.getLogger("agent_harness_worker")
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s %(name)s run_id=%(run_id)s trace_id=%(trace_id)s %(message)s"
            )
        )
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
            executor.execute(run)
    except ExecutionCancelled as exc:
        cancelled = store.mark_run_cancelled(run.run_id, str(exc))
        if observability is not None:
            observability.metrics.record_run_terminal(cancelled)
    except Exception as exc:
        failed = store.mark_run_failed(run.run_id, str(exc))
        if observability is not None:
            observability.metrics.record_run_terminal(failed)
    if observability is not None:
        observability.metrics.refresh_queue(store.list_runs())
    return True


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
