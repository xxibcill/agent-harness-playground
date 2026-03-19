from __future__ import annotations

import logging
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class WorkerConfig:
    poll_interval_seconds: float = 5.0


def build_logger() -> logging.Logger:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    return logging.getLogger("agent_harness_worker")


def main() -> None:
    config = WorkerConfig()
    logger = build_logger()
    logger.info("Worker scaffold started. Poll interval: %.1fs", config.poll_interval_seconds)
    logger.info("Queue integration will be added in Task 2.")
    time.sleep(0)


if __name__ == "__main__":
    main()

