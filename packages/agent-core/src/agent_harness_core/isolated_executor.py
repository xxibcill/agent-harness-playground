"""Process-based isolation for agent harness runs.

Each run executes in a separate subprocess with resource limits,
while events are relayed back to the parent process for persistence.
"""
from __future__ import annotations

import logging
import multiprocessing as mp
import resource
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import TYPE_CHECKING, Any, cast

from agent_harness_contracts import RunEvent, RunRecord, RunStatus

if TYPE_CHECKING:
    from agent_harness_observability import ServiceObservability

    from agent_harness_core.runtime import RunStore

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _to_iso8601(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat()


@dataclass(frozen=True)
class ResourceLimits:
    """Resource limits to apply to subprocess execution."""

    cpu_seconds: int = 300  # 5 min CPU time
    memory_mb: int = 512  # 512 MB

    def apply(self) -> None:
        """Apply rlimits to current process."""
        # CPU time limit (soft, hard)
        resource.setrlimit(
            resource.RLIMIT_CPU,
            (self.cpu_seconds, self.cpu_seconds + 1),
        )

        # Memory limit via address space (soft, hard)
        # Note: RLIMIT_AS behavior differs on macOS vs Linux
        memory_bytes = self.memory_mb * 1024 * 1024
        try:
            resource.setrlimit(
                resource.RLIMIT_AS,
                (memory_bytes, memory_bytes),
            )
        except ValueError:
            # macOS may reject certain values; log and continue
            logger.warning(
                "Could not set RLIMIT_AS to %d bytes. "
                "Memory limits may not be enforced on this platform.",
                memory_bytes,
            )


@dataclass
class SubprocessEvent:
    """Event sent from subprocess to parent via Queue."""

    event_type: str  # "event" | "result" | "error"
    payload: dict[str, Any] | None = None
    category: str | None = None
    node_name: str | None = None
    tool_name: str | None = None
    model_name: str | None = None


@dataclass
class SubprocessResult:
    """Final result from subprocess execution."""

    status: str  # RunStatus value
    output: dict[str, Any] | None = None
    error: str | None = None


class EventRelay(threading.Thread):
    """Reads events from Queue and writes them to the store."""

    def __init__(
        self,
        event_queue: mp.Queue,
        store: RunStore,
        run_id: str,
    ) -> None:
        super().__init__(
            name=f"event-relay-{run_id}",
            daemon=True,
        )
        self._event_queue = event_queue
        self._store = store
        self._run_id = run_id
        self._stop_event = threading.Event()
        self.events_relayed = 0
        self.result: SubprocessResult | None = None

    def stop(self) -> None:
        """Signal the relay to stop."""
        self._stop_event.set()

    def run(self) -> None:
        """Process events from queue until stopped or queue closes."""
        while not self._stop_event.is_set():
            try:
                # Use timeout to allow checking stop_event periodically
                event = self._event_queue.get(timeout=0.1)
            except Exception:
                # Queue empty or closed
                continue

            if event is None:
                # Sentinel value signals end of events
                break

            if isinstance(event, SubprocessResult):
                # Store the result for the parent process to apply
                self.result = event
                break

            if not isinstance(event, SubprocessEvent):
                logger.warning(
                    "Received unexpected type from event queue: %s",
                    type(event).__name__,
                )
                continue

            try:
                self._store.append_event(
                    self._run_id,
                    event_type=event.event_type,
                    category=event.category or "run",
                    payload=event.payload,
                    node_name=event.node_name,
                    tool_name=event.tool_name,
                    model_name=event.model_name,
                )
                self.events_relayed += 1
            except Exception as exc:
                logger.exception(
                    "Failed to relay event to store: %s",
                    exc,
                )


class IsolatedExecutor:
    """Executes runs in isolated subprocesses with resource limits."""

    def __init__(
        self,
        store: RunStore,
        observability: ServiceObservability | None = None,
        resource_limits: ResourceLimits | None = None,
    ) -> None:
        self._store = store
        self._observability = observability
        self._resource_limits = resource_limits

    def execute(self, run: RunRecord) -> RunRecord:
        """Execute a run in an isolated subprocess.

        Args:
            run: The run record to execute.

        Returns:
            The final run state after execution.
        """
        if self._resource_limits is None:
            # No isolation - run directly in current process
            from agent_harness_core.executor import RuntimeExecutor

            executor = RuntimeExecutor(
                self._store,
                observability=self._observability,
            )
            return executor.execute(run)

        # Create queue for event passing
        # Use mp.Queue which is thread and process safe
        event_queue: mp.Queue = mp.Queue()

        # Start event relay thread
        relay = EventRelay(event_queue, self._store, run.run_id)
        relay.start()

        try:
            # Prepare subprocess arguments
            args = (
                run.model_dump(mode="json"),
                self._resource_limits,
            )

            # Spawn subprocess
            process = mp.Process(
                target=_run_in_subprocess,
                args=(args, event_queue),
                name=f"run-{run.run_id}",
            )
            process.start()

            # Wait for completion with timeout
            # Use run's timeout_seconds as the upper bound
            timeout = run.timeout_seconds + 30  # Extra buffer for cleanup
            process.join(timeout=timeout)

            if process.is_alive():
                # Process timed out - terminate it
                logger.warning(
                    "Run %s exceeded timeout, terminating subprocess",
                    run.run_id,
                )
                process.terminate()
                process.join(timeout=5)

                if process.is_alive():
                    # Force kill if terminate didn't work
                    logger.warning(
                        "Run %s did not terminate gracefully, killing",
                        run.run_id,
                    )
                    process.kill()
                    process.join(timeout=5)

                # Mark as failed if not already done
                return self._store.mark_run_failed(
                    run.run_id,
                    f"Run exceeded timeout of {run.timeout_seconds} seconds",
                )

            # Wait for relay to finish processing events
            relay.join(timeout=5.0)

            # Apply the final result from subprocess
            if relay.result is not None:
                result = relay.result
                if result.status == RunStatus.COMPLETED.value:
                    return self._store.mark_run_completed(
                        run.run_id,
                        result.output or {},
                    )
                elif result.status == RunStatus.FAILED.value:
                    return self._store.mark_run_failed(
                        run.run_id,
                        result.error or "Unknown error",
                    )
                elif result.status == RunStatus.CANCELLED.value:
                    return self._store.mark_run_cancelled(
                        run.run_id,
                        result.error or "Cancelled",
                    )

            # Get the final run state from the store as fallback
            final_run = self._store.get_run(run.run_id)
            if final_run is not None and final_run.status.is_terminal:
                return final_run

            # If subprocess exited but didn't update the run, mark as failed
            return self._store.mark_run_failed(
                run.run_id,
                "Subprocess exited without completing the run",
            )

        finally:
            # Signal relay to stop and wait for it
            relay.stop()
            try:
                # Put sentinel to unblock relay if it's waiting
                event_queue.put_nowait(None)
            except Exception:
                pass
            relay.join(timeout=2.0)

            # Close the queue
            try:
                event_queue.close()
                event_queue.join_thread()
            except Exception:
                pass


def _run_in_subprocess(
    args: tuple[dict[str, Any], ResourceLimits],
    event_queue: mp.Queue,
) -> None:
    """Entry point for subprocess execution.

    This function runs in the subprocess:
    1. Applies resource limits
    2. Creates a QueueBackedStore wrapper
    3. Executes the run via RuntimeExecutor
    4. Sends final result via queue

    Args:
        args: Tuple of (run_dict, resource_limits)
        event_queue: Queue for sending events to parent
    """
    run_dict, resource_limits = args

    # Apply resource limits
    try:
        resource_limits.apply()
    except Exception as exc:
        logger.exception("Failed to apply resource limits: %s", exc)
        # Continue anyway - limits are best effort

    # Import here to avoid issues with multiprocessing on some platforms
    from agent_harness_core.executor import RuntimeExecutor

    # Create a queue-backed store that forwards events
    run = RunRecord.model_validate(run_dict)
    queue_store = _QueueBackedStore(run, event_queue)

    # Execute the run
    try:
        executor = RuntimeExecutor(queue_store)
        executor.execute(run)
    except Exception as exc:
        # The RuntimeExecutor handles most errors internally
        # Log for debugging but the error handling is done by the executor
        logger.exception("Subprocess execution failed: %s", exc)
        raise


class _QueueBackedStore:
    """A minimal store that forwards events to a queue.

    This store is used in the subprocess and:
    - Stores run data with string dates (as serialized)
    - Forwards all events to the parent process via queue
    - Implements the RunStore protocol for RuntimeExecutor
    """

    def __init__(self, run: RunRecord, event_queue: mp.Queue) -> None:
        self._run = run
        self._event_queue = event_queue
        self._runs: dict[str, dict[str, Any]] = {
            run.run_id: run.model_dump(),
        }
        self._events: dict[str, list[dict[str, Any]]] = {
            run.run_id: [],
        }
        self._lock = Lock()
        self._event_sequence = 0

    def apply_migrations(self) -> None:
        """No migrations needed for in-memory store."""
        return None

    def create_run(self, request: Any) -> RunRecord:
        """Not used in subprocess - raise if called."""
        raise NotImplementedError("create_run not supported in subprocess")

    def list_runs(self) -> list[RunRecord]:
        """List all runs."""
        with self._lock:
            return [self._build_run(run) for run in self._runs.values()]

    def get_run(self, run_id: str) -> RunRecord | None:
        """Get run by ID."""
        with self._lock:
            run = self._runs.get(run_id)
            return None if run is None else self._build_run(run)

    def set_run_trace_context(
        self, run_id: str, trace_id: str, traceparent: str
    ) -> RunRecord:
        """Set trace context on run."""
        with self._lock:
            run = self._require_run(run_id)
            run["trace_id"] = trace_id
            run["traceparent"] = traceparent
            run["updated_at"] = _to_iso8601(_utc_now())
            return self._build_run(run)

    def cancel_run(self, run_id: str) -> RunRecord | None:
        """Cancel a run."""
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                return None
            status = RunStatus(run["status"])
            if status.is_terminal:
                return self._build_run(run)

            now = _to_iso8601(_utc_now())
            run["updated_at"] = now
            run["cancel_requested_at"] = now
            if status == RunStatus.QUEUED:
                run["status"] = RunStatus.CANCELLED.value
                run["completed_at"] = now
                run["lease_expires_at"] = None
            else:
                run["status"] = RunStatus.CANCELLING.value
            return self._build_run(run)

    def claim_next_run(self, worker_id: str, lease_seconds: int) -> RunRecord | None:
        """Not used in subprocess - raise if called."""
        raise NotImplementedError("claim_next_run not supported in subprocess")

    def refresh_lease(
        self, run_id: str, worker_id: str, lease_seconds: int
    ) -> RunRecord | None:
        """Refresh lease on a run."""
        with self._lock:
            run = self._runs.get(run_id)
            if run is None or run.get("worker_id") != worker_id:
                return None
            run["updated_at"] = _to_iso8601(_utc_now())
            return self._build_run(run)

    def requeue_run(
        self, run_id: str, error: str, scheduled_at: datetime
    ) -> RunRecord:
        """Requeue a run for retry."""
        with self._lock:
            run = self._require_run(run_id)
            now = _to_iso8601(_utc_now())
            run["status"] = RunStatus.QUEUED.value
            run["error"] = error
            run["scheduled_at"] = _to_iso8601(scheduled_at) or ""
            run["updated_at"] = now
            run["worker_id"] = None
            run["lease_expires_at"] = None
            return self._build_run(run)

    def list_events(self, run_id: str, after_sequence: int = 0) -> list[RunEvent]:
        """List events for a run."""
        with self._lock:
            events = self._events.get(run_id, [])
            return [
                self._build_event(event)
                for event in events
                if cast(int, event["sequence"]) > after_sequence
            ]

    def append_event(
        self,
        run_id: str,
        *,
        event_type: str,
        category: str,
        payload: dict[str, Any] | None = None,
        node_name: str | None = None,
        tool_name: str | None = None,
        model_name: str | None = None,
    ) -> RunEvent:
        """Append event to local store and forward to parent via queue."""
        with self._lock:
            run = self._require_run(run_id)
            self._event_sequence += 1
            now = _utc_now()
            event = {
                "event_id": f"evt_{uuid.uuid4().hex}",
                "run_id": run_id,
                "sequence": self._event_sequence,
                "event_type": event_type,
                "category": category,
                "created_at": _to_iso8601(now),
                "node_name": node_name,
                "tool_name": tool_name,
                "model_name": model_name,
                "trace_id": run.get("trace_id"),
                "span_id": None,
                "parent_span_id": None,
                "payload": payload or {},
            }
            self._events[run_id].append(event)

        # Forward to parent process
        try:
            subprocess_event = SubprocessEvent(
                event_type=event_type,
                payload=payload,
                category=category,
                node_name=node_name,
                tool_name=tool_name,
                model_name=model_name,
            )
            self._event_queue.put(subprocess_event)
        except Exception as exc:
            logger.warning(
                "Failed to send event to parent process: %s",
                exc,
            )

        return self._build_event(event)

    def mark_run_completed(self, run_id: str, output: dict[str, Any]) -> RunRecord:
        """Mark run as completed and send result to parent."""
        with self._lock:
            run = self._require_run(run_id)
            now = _to_iso8601(_utc_now())
            run["status"] = RunStatus.COMPLETED.value
            run["output"] = output
            run["completed_at"] = now
            run["updated_at"] = now
            run["lease_expires_at"] = None
            result = self._build_run(run)

        # Send result to parent process
        try:
            subprocess_result = SubprocessResult(
                status=RunStatus.COMPLETED.value,
                output=output,
            )
            self._event_queue.put(subprocess_result)
        except Exception as exc:
            logger.warning(
                "Failed to send result to parent process: %s",
                exc,
            )

        return result

    def mark_run_failed(self, run_id: str, error: str) -> RunRecord:
        """Mark run as failed and send result to parent."""
        with self._lock:
            run = self._require_run(run_id)
            now = _to_iso8601(_utc_now())
            run["status"] = RunStatus.FAILED.value
            run["error"] = error
            run["completed_at"] = now
            run["updated_at"] = now
            run["lease_expires_at"] = None
            result = self._build_run(run)

        # Send result to parent process
        try:
            subprocess_result = SubprocessResult(
                status=RunStatus.FAILED.value,
                error=error,
            )
            self._event_queue.put(subprocess_result)
        except Exception as exc:
            logger.warning(
                "Failed to send result to parent process: %s",
                exc,
            )

        return result

    def mark_run_cancelled(self, run_id: str, reason: str) -> RunRecord:
        """Mark run as cancelled and send result to parent."""
        with self._lock:
            run = self._require_run(run_id)
            now = _to_iso8601(_utc_now())
            run["status"] = RunStatus.CANCELLED.value
            run["error"] = reason
            run["completed_at"] = now
            run["updated_at"] = now
            run["lease_expires_at"] = None
            result = self._build_run(run)

        # Send result to parent process
        try:
            subprocess_result = SubprocessResult(
                status=RunStatus.CANCELLED.value,
                error=reason,
            )
            self._event_queue.put(subprocess_result)
        except Exception as exc:
            logger.warning(
                "Failed to send result to parent process: %s",
                exc,
            )

        return result

    def _require_run(self, run_id: str) -> dict[str, Any]:
        """Get run or raise if not found."""
        run = self._runs.get(run_id)
        if run is None:
            raise KeyError(f"Run {run_id} does not exist.")
        return run

    def _build_run(self, run: dict[str, Any]) -> RunRecord:
        """Build RunRecord from internal dict."""
        return RunRecord(
            run_id=cast(str, run["run_id"]),
            workflow=cast(str, run["workflow"]),
            status=RunStatus(cast(str, run["status"])),
            input=cast(str, run["input"]),
            metadata=cast(dict[str, Any], run.get("metadata", {})),
            workflow_config=self._load_workflow_config(run.get("workflow_config")),
            output=cast(dict[str, Any] | None, run.get("output")),
            error=cast(str | None, run.get("error")),
            scheduled_at=cast(str, run.get("scheduled_at", "")),
            created_at=cast(str, run.get("created_at", "")),
            updated_at=cast(str, run.get("updated_at", "")),
            started_at=cast(str | None, run.get("started_at")),
            completed_at=cast(str | None, run.get("completed_at")),
            cancel_requested_at=cast(str | None, run.get("cancel_requested_at")),
            attempt_count=cast(int, run.get("attempt_count", 0)),
            max_attempts=cast(int, run.get("max_attempts", 3)),
            timeout_seconds=cast(int, run.get("timeout_seconds", 300)),
            worker_id=cast(str | None, run.get("worker_id")),
            lease_expires_at=cast(str | None, run.get("lease_expires_at")),
            trace_id=cast(str | None, run.get("trace_id")),
            traceparent=cast(str | None, run.get("traceparent")),
        )

    def _load_workflow_config(self, payload: Any) -> Any:
        """Load workflow config from payload."""
        from agent_harness_contracts import WorkflowConfig

        return WorkflowConfig.model_validate(payload or {})

    def _build_event(self, event: dict[str, Any]) -> RunEvent:
        """Build RunEvent from internal dict."""
        return RunEvent(
            event_id=cast(str, event["event_id"]),
            run_id=cast(str, event["run_id"]),
            sequence=cast(int, event["sequence"]),
            event_type=cast(str, event["event_type"]),
            category=cast(str, event["category"]),
            created_at=cast(str, event["created_at"]),
            node_name=cast(str | None, event.get("node_name")),
            tool_name=cast(str | None, event.get("tool_name")),
            model_name=cast(str | None, event.get("model_name")),
            trace_id=cast(str | None, event.get("trace_id")),
            span_id=cast(str | None, event.get("span_id")),
            parent_span_id=cast(str | None, event.get("parent_span_id")),
            payload=cast(dict[str, Any], event.get("payload", {})),
        )
