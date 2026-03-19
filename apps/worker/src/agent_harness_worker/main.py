from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass

from agent_harness_core import ExecutionCancelled, RuntimeExecutor, RunStore, build_run_store


@dataclass(frozen=True)
class WorkerConfig:
    poll_interval_seconds: float = float(os.getenv("AGENT_HARNESS_WORKER_POLL_SECONDS", "1.0"))
    lease_seconds: int = int(os.getenv("AGENT_HARNESS_WORKER_LEASE_SECONDS", "30"))
    worker_id: str = os.getenv("AGENT_HARNESS_WORKER_ID", "worker-local")


def build_logger() -> logging.Logger:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    return logging.getLogger("agent_harness_worker")


def run_once(store: RunStore, executor: RuntimeExecutor, config: WorkerConfig) -> bool:
    run = store.claim_next_run(config.worker_id, config.lease_seconds)
    if run is None:
        return False

    store.refresh_lease(run.run_id, config.worker_id, config.lease_seconds)
    try:
        executor.execute(run)
    except ExecutionCancelled as exc:
        store.mark_run_cancelled(run.run_id, str(exc))
    except Exception as exc:
        store.mark_run_failed(run.run_id, str(exc))
    return True


def main() -> None:
    config = WorkerConfig()
    logger = build_logger()
    store = build_run_store()
    store.apply_migrations()
    executor = RuntimeExecutor(store)
    logger.info(
        "Worker started. worker_id=%s poll_interval_seconds=%.1f lease_seconds=%s",
        config.worker_id,
        config.poll_interval_seconds,
        config.lease_seconds,
    )

    while True:
        did_work = run_once(store, executor, config)
        if not did_work:
            time.sleep(config.poll_interval_seconds)


if __name__ == "__main__":
    main()
