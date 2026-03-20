from __future__ import annotations

import json
import os
import uuid
from collections.abc import Iterable
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from importlib import resources
from pathlib import Path
from threading import Lock
from typing import Any, Protocol, cast

import psycopg
from agent_harness_contracts import CreateRunRequest, RunEvent, RunRecord, RunStatus, TokenUsage
from agent_harness_observability import capture_current_trace
from psycopg.rows import dict_row


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def to_iso8601(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat()


def parse_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value)


@dataclass(frozen=True)
class RunStoreConfig:
    database_url: str = os.getenv(
        "AGENT_HARNESS_DATABASE_URL",
        "postgresql://agent_harness:agent_harness@127.0.0.1:5432/agent_harness",
    )
    application_name: str = os.getenv("AGENT_HARNESS_APP_NAME", "agent-harness")


class RunStore(Protocol):
    def apply_migrations(self) -> None: ...

    def create_run(self, request: CreateRunRequest) -> RunRecord: ...

    def list_runs(self) -> list[RunRecord]: ...

    def get_run(self, run_id: str) -> RunRecord | None: ...

    def set_run_trace_context(self, run_id: str, trace_id: str, traceparent: str) -> RunRecord: ...

    def cancel_run(self, run_id: str) -> RunRecord | None: ...

    def claim_next_run(self, worker_id: str, lease_seconds: int) -> RunRecord | None: ...

    def refresh_lease(
        self,
        run_id: str,
        worker_id: str,
        lease_seconds: int,
    ) -> RunRecord | None: ...

    def requeue_run(self, run_id: str, error: str, scheduled_at: datetime) -> RunRecord: ...

    def list_events(self, run_id: str, after_sequence: int = 0) -> list[RunEvent]: ...

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
    ) -> RunEvent: ...

    def mark_run_completed(self, run_id: str, output: dict[str, Any]) -> RunRecord: ...

    def mark_run_failed(self, run_id: str, error: str) -> RunRecord: ...

    def mark_run_cancelled(self, run_id: str, reason: str) -> RunRecord: ...


def build_run_store(config: RunStoreConfig | None = None) -> RunStore:
    return PostgresRunStore(config or RunStoreConfig())


class InMemoryRunStore:
    def __init__(self) -> None:
        self._runs: dict[str, dict[str, Any]] = {}
        self._events: dict[str, list[dict[str, Any]]] = {}
        self._lock = Lock()

    def apply_migrations(self) -> None:
        return None

    def create_run(self, request: CreateRunRequest) -> RunRecord:
        now = utc_now()
        scheduled_at = parse_datetime(request.scheduled_at) or now
        run_id = f"run_{uuid.uuid4().hex}"
        trace = capture_current_trace()
        with self._lock:
            payload = {
                "run_id": run_id,
                "workflow": request.workflow,
                "status": RunStatus.QUEUED.value,
                "input": request.input,
                "metadata": dict(request.metadata),
                "output": None,
                "error": None,
                "scheduled_at": scheduled_at,
                "created_at": now,
                "updated_at": now,
                "started_at": None,
                "completed_at": None,
                "cancel_requested_at": None,
                "attempt_count": 0,
                "max_attempts": request.max_attempts,
                "timeout_seconds": request.timeout_seconds,
                "worker_id": None,
                "lease_expires_at": None,
                "event_sequence": 0,
                "lease_owner": None,
                "trace_id": trace.trace_id,
                "traceparent": trace.traceparent,
            }
            self._runs[run_id] = payload
            self._events[run_id] = []
            self._append_event_locked(
                run_id,
                event_type="run.created",
                category="run",
                payload={"workflow": request.workflow},
            )
            self._append_event_locked(
                run_id,
                event_type="run.queued",
                category="run",
                payload={"scheduled_at": to_iso8601(scheduled_at)},
            )
            return self._build_run(payload)

    def list_runs(self) -> list[RunRecord]:
        with self._lock:
            runs = sorted(
                self._runs.values(),
                key=lambda item: cast(datetime, item["created_at"]),
                reverse=True,
            )
            return [self._build_run(run) for run in runs]

    def get_run(self, run_id: str) -> RunRecord | None:
        with self._lock:
            run = self._runs.get(run_id)
            return None if run is None else self._build_run(run)

    def set_run_trace_context(self, run_id: str, trace_id: str, traceparent: str) -> RunRecord:
        with self._lock:
            run = self._require_run(run_id)
            run["trace_id"] = trace_id
            run["traceparent"] = traceparent
            run["updated_at"] = utc_now()
            return self._build_run(run)

    def cancel_run(self, run_id: str) -> RunRecord | None:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                return None
            if RunStatus(run["status"]).is_terminal:
                return self._build_run(run)

            now = utc_now()
            run["updated_at"] = now
            run["cancel_requested_at"] = now
            if run["status"] == RunStatus.QUEUED.value:
                run["status"] = RunStatus.CANCELLED.value
                run["completed_at"] = now
                run["lease_expires_at"] = None
                self._append_event_locked(
                    run_id,
                    event_type="run.cancelled",
                    category="run",
                    payload={"reason": "Cancelled before execution started."},
                )
            else:
                run["status"] = RunStatus.CANCELLING.value
                self._append_event_locked(
                    run_id,
                    event_type="run.cancel_requested",
                    category="run",
                    payload={"reason": "Cancellation requested by API."},
                )
            return self._build_run(run)

    def claim_next_run(self, worker_id: str, lease_seconds: int) -> RunRecord | None:
        now = utc_now()
        lease_expires_at = now + timedelta(seconds=lease_seconds)
        with self._lock:
            candidates = sorted(
                self._runs.values(),
                key=lambda item: cast(datetime, item["created_at"]),
            )
            for run in candidates:
                status = RunStatus(run["status"])
                is_stale_running = status in {
                    RunStatus.RUNNING,
                    RunStatus.CANCELLING,
                } and cast(datetime | None, run["lease_expires_at"]) is not None and cast(
                    datetime, run["lease_expires_at"]
                ) <= now
                if status not in {RunStatus.QUEUED, RunStatus.RUNNING, RunStatus.CANCELLING}:
                    continue
                if cast(int, run["attempt_count"]) >= cast(int, run["max_attempts"]):
                    continue
                if status in {RunStatus.RUNNING, RunStatus.CANCELLING} and not is_stale_running:
                    continue
                run["status"] = (
                    RunStatus.CANCELLING.value
                    if run["cancel_requested_at"] is not None
                    else RunStatus.RUNNING.value
                )
                run["attempt_count"] += 1
                run["worker_id"] = worker_id
                run["lease_owner"] = worker_id
                run["lease_expires_at"] = lease_expires_at
                run["updated_at"] = now
                run["started_at"] = run["started_at"] or now
                self._append_event_locked(
                    run["run_id"],
                    event_type="run.started",
                    category="run",
                    payload={"worker_id": worker_id, "attempt_count": run["attempt_count"]},
                )
                return self._build_run(run)
        return None

    def refresh_lease(self, run_id: str, worker_id: str, lease_seconds: int) -> RunRecord | None:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None or run["worker_id"] != worker_id:
                return None
            now = utc_now()
            run["lease_expires_at"] = now + timedelta(seconds=lease_seconds)
            run["updated_at"] = now
            return self._build_run(run)

    def requeue_run(self, run_id: str, error: str, scheduled_at: datetime) -> RunRecord:
        with self._lock:
            run = self._require_run(run_id)
            now = utc_now()
            run["status"] = RunStatus.QUEUED.value
            run["error"] = error
            run["scheduled_at"] = scheduled_at
            run["updated_at"] = now
            run["worker_id"] = None
            run["lease_owner"] = None
            run["lease_expires_at"] = None
            self._append_event_locked(
                run_id,
                event_type="run.retry_scheduled",
                category="run",
                payload={
                    "error": error,
                    "scheduled_at": to_iso8601(scheduled_at),
                    "attempt_count": run["attempt_count"],
                    "max_attempts": run["max_attempts"],
                },
            )
            return self._build_run(run)

    def list_events(self, run_id: str, after_sequence: int = 0) -> list[RunEvent]:
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
        with self._lock:
            event = self._append_event_locked(
                run_id,
                event_type=event_type,
                category=category,
                payload=payload,
                node_name=node_name,
                tool_name=tool_name,
                model_name=model_name,
            )
            return self._build_event(event)

    def mark_run_completed(self, run_id: str, output: dict[str, Any]) -> RunRecord:
        with self._lock:
            run = self._require_run(run_id)
            now = utc_now()
            run["status"] = RunStatus.COMPLETED.value
            run["output"] = output
            run["completed_at"] = now
            run["updated_at"] = now
            run["lease_expires_at"] = None
            self._append_event_locked(
                run_id,
                event_type="run.completed",
                category="run",
                payload=output,
            )
            return self._build_run(run)

    def mark_run_failed(self, run_id: str, error: str) -> RunRecord:
        with self._lock:
            run = self._require_run(run_id)
            now = utc_now()
            run["status"] = RunStatus.FAILED.value
            run["error"] = error
            run["completed_at"] = now
            run["updated_at"] = now
            run["lease_expires_at"] = None
            self._append_event_locked(
                run_id,
                event_type="run.failed",
                category="run",
                payload={"error": error},
            )
            return self._build_run(run)

    def mark_run_cancelled(self, run_id: str, reason: str) -> RunRecord:
        with self._lock:
            run = self._require_run(run_id)
            now = utc_now()
            run["status"] = RunStatus.CANCELLED.value
            run["error"] = reason
            run["completed_at"] = now
            run["updated_at"] = now
            run["lease_expires_at"] = None
            self._append_event_locked(
                run_id,
                event_type="run.cancelled",
                category="run",
                payload={"reason": reason},
            )
            return self._build_run(run)

    def _append_event_locked(
        self,
        run_id: str,
        *,
        event_type: str,
        category: str,
        payload: dict[str, Any] | None = None,
        node_name: str | None = None,
        tool_name: str | None = None,
        model_name: str | None = None,
    ) -> dict[str, Any]:
        run = self._require_run(run_id)
        trace = capture_current_trace()
        run["event_sequence"] += 1
        event = {
            "event_id": f"evt_{uuid.uuid4().hex}",
            "run_id": run_id,
            "sequence": run["event_sequence"],
            "event_type": event_type,
            "category": category,
            "created_at": utc_now(),
            "node_name": node_name,
            "tool_name": tool_name,
            "model_name": model_name,
            "trace_id": trace.trace_id or cast(str | None, run["trace_id"]),
            "span_id": trace.span_id,
            "parent_span_id": trace.parent_span_id,
            "payload": payload or {},
        }
        self._events[run_id].append(event)
        return event

    def _require_run(self, run_id: str) -> dict[str, Any]:
        run = self._runs.get(run_id)
        if run is None:
            raise KeyError(f"Run {run_id} does not exist.")
        return run

    def _build_run(self, run: dict[str, Any]) -> RunRecord:
        return RunRecord(
            run_id=cast(str, run["run_id"]),
            workflow=cast(str, run["workflow"]),
            status=RunStatus(cast(str, run["status"])),
            input=cast(str, run["input"]),
            metadata=cast(dict[str, Any], run["metadata"]),
            output=cast(dict[str, Any] | None, run["output"]),
            error=cast(str | None, run["error"]),
            scheduled_at=to_iso8601(cast(datetime, run["scheduled_at"])) or "",
            created_at=to_iso8601(cast(datetime, run["created_at"])) or "",
            updated_at=to_iso8601(cast(datetime, run["updated_at"])) or "",
            started_at=to_iso8601(cast(datetime | None, run["started_at"])),
            completed_at=to_iso8601(cast(datetime | None, run["completed_at"])),
            cancel_requested_at=to_iso8601(cast(datetime | None, run["cancel_requested_at"])),
            attempt_count=cast(int, run["attempt_count"]),
            max_attempts=cast(int, run["max_attempts"]),
            timeout_seconds=cast(int, run["timeout_seconds"]),
            worker_id=cast(str | None, run["worker_id"]),
            lease_expires_at=to_iso8601(cast(datetime | None, run["lease_expires_at"])),
            trace_id=cast(str | None, run["trace_id"]),
            traceparent=cast(str | None, run["traceparent"]),
        )

    def _build_event(self, event: dict[str, Any]) -> RunEvent:
        return RunEvent(
            event_id=cast(str, event["event_id"]),
            run_id=cast(str, event["run_id"]),
            sequence=cast(int, event["sequence"]),
            event_type=cast(str, event["event_type"]),
            category=cast(str, event["category"]),
            created_at=to_iso8601(cast(datetime, event["created_at"])) or "",
            node_name=cast(str | None, event["node_name"]),
            tool_name=cast(str | None, event["tool_name"]),
            model_name=cast(str | None, event["model_name"]),
            trace_id=cast(str | None, event["trace_id"]),
            span_id=cast(str | None, event["span_id"]),
            parent_span_id=cast(str | None, event["parent_span_id"]),
            payload=cast(dict[str, Any], event["payload"]),
        )


class PostgresRunStore:
    def __init__(self, config: RunStoreConfig) -> None:
        self._config = config

    def apply_migrations(self) -> None:
        migration_dir = resources.files("agent_harness_core").joinpath("migrations")
        migration_paths = sorted(
            cast(Iterable[Path], migration_dir.iterdir()),
            key=lambda item: item.name,
        )
        for migration_path in migration_paths:
            self._apply_migration_file(migration_path)

    def create_run(self, request: CreateRunRequest) -> RunRecord:
        run_id = f"run_{uuid.uuid4().hex}"
        trace = capture_current_trace()
        with self._connection() as connection, connection.transaction():
            scheduled_at = parse_datetime(request.scheduled_at) or utc_now()
            row = connection.execute(
                """
                insert into runtime_runs (
                    run_id,
                    workflow_name,
                    status,
                    input_text,
                    metadata,
                    scheduled_at,
                    max_attempts,
                    timeout_seconds,
                    trace_id,
                    traceparent
                )
                values (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s)
                returning *
                """,
                (
                    run_id,
                    request.workflow,
                    RunStatus.QUEUED.value,
                    request.input,
                    json.dumps(request.metadata),
                    scheduled_at,
                    request.max_attempts,
                    request.timeout_seconds,
                    trace.trace_id,
                    trace.traceparent,
                ),
            ).fetchone()
            self._insert_event(
                connection,
                run_id,
                event_type="run.created",
                category="run",
                payload={"workflow": request.workflow},
            )
            self._insert_event(
                connection,
                run_id,
                event_type="run.queued",
                category="run",
                payload={"scheduled_at": to_iso8601(scheduled_at)},
            )
            return self._build_run(dict(row))

    def list_runs(self) -> list[RunRecord]:
        with self._connection() as connection:
            rows = connection.execute(
                "select * from runtime_runs order by created_at desc"
            ).fetchall()
        return [self._build_run(dict(row)) for row in rows]

    def get_run(self, run_id: str) -> RunRecord | None:
        with self._connection() as connection:
            row = connection.execute(
                "select * from runtime_runs where run_id = %s",
                (run_id,),
            ).fetchone()
        return None if row is None else self._build_run(dict(row))

    def set_run_trace_context(self, run_id: str, trace_id: str, traceparent: str) -> RunRecord:
        with self._connection() as connection, connection.transaction():
            row = connection.execute(
                """
                update runtime_runs
                set trace_id = %s,
                    traceparent = %s,
                    updated_at = now()
                where run_id = %s
                returning *
                """,
                (trace_id, traceparent, run_id),
            ).fetchone()
        if row is None:
            raise KeyError(f"Run {run_id} does not exist.")
        return self._build_run(dict(row))

    def cancel_run(self, run_id: str) -> RunRecord | None:
        with self._connection() as connection, connection.transaction():
            row = connection.execute(
                "select * from runtime_runs where run_id = %s for update",
                (run_id,),
            ).fetchone()
            if row is None:
                return None

            run = dict(row)
            status = RunStatus(run["status"])
            if status.is_terminal:
                return self._build_run(run)

            now = utc_now()
            if status == RunStatus.QUEUED:
                updated = connection.execute(
                    """
                    update runtime_runs
                    set status = %s,
                        error_text = %s,
                        cancel_requested_at = %s,
                        completed_at = %s,
                        updated_at = %s,
                        lease_expires_at = null
                    where run_id = %s
                    returning *
                    """,
                    (
                        RunStatus.CANCELLED.value,
                        "Cancelled before execution started.",
                        now,
                        now,
                        now,
                        run_id,
                    ),
                ).fetchone()
                self._insert_event(
                    connection,
                    run_id,
                    event_type="run.cancelled",
                    category="run",
                    payload={"reason": "Cancelled before execution started."},
                )
                return self._build_run(dict(updated))

            updated = connection.execute(
                """
                update runtime_runs
                set status = %s,
                    cancel_requested_at = %s,
                    updated_at = %s
                where run_id = %s
                returning *
                """,
                (
                    RunStatus.CANCELLING.value,
                    now,
                    now,
                    run_id,
                ),
            ).fetchone()
            self._insert_event(
                connection,
                run_id,
                event_type="run.cancel_requested",
                category="run",
                payload={"reason": "Cancellation requested by API."},
            )
            return self._build_run(dict(updated))

    def claim_next_run(self, worker_id: str, lease_seconds: int) -> RunRecord | None:
        with self._connection() as connection, connection.transaction():
            row = connection.execute(
                """
                select *
                from runtime_runs
                where (
                    (status = %s and attempt_count < max_attempts)
                    or (
                        status in (%s, %s)
                        and lease_expires_at <= now()
                        and attempt_count < max_attempts
                    )
                )
                  and scheduled_at <= now()
                order by created_at asc
                for update skip locked
                limit 1
                """,
                (
                    RunStatus.QUEUED.value,
                    RunStatus.RUNNING.value,
                    RunStatus.CANCELLING.value,
                ),
            ).fetchone()
            if row is None:
                return None

            current = dict(row)
            next_status = (
                RunStatus.CANCELLING.value
                if current["cancel_requested_at"] is not None
                else RunStatus.RUNNING.value
            )
            updated = connection.execute(
                """
                update runtime_runs
                set status = %s,
                    worker_id = %s,
                    attempt_count = attempt_count + 1,
                    started_at = coalesce(started_at, now()),
                    updated_at = now(),
                    lease_expires_at = now() + (%s * interval '1 second')
                where run_id = %s
                returning *
                """,
                (
                    next_status,
                    worker_id,
                    lease_seconds,
                    current["run_id"],
                ),
            ).fetchone()
            self._insert_event(
                connection,
                current["run_id"],
                event_type="run.started",
                category="run",
                payload={"worker_id": worker_id, "attempt_count": updated["attempt_count"]},
            )
            return self._build_run(dict(updated))

    def refresh_lease(self, run_id: str, worker_id: str, lease_seconds: int) -> RunRecord | None:
        with self._connection() as connection, connection.transaction():
            row = connection.execute(
                """
                update runtime_runs
                set updated_at = now(),
                    lease_expires_at = now() + (%s * interval '1 second')
                where run_id = %s and worker_id = %s and status in (%s, %s)
                returning *
                """,
                (
                    lease_seconds,
                    run_id,
                    worker_id,
                    RunStatus.RUNNING.value,
                    RunStatus.CANCELLING.value,
                ),
            ).fetchone()
        return None if row is None else self._build_run(dict(row))

    def requeue_run(self, run_id: str, error: str, scheduled_at: datetime) -> RunRecord:
        with self._connection() as connection, connection.transaction():
            row = connection.execute(
                """
                update runtime_runs
                set status = %s,
                    error_text = %s,
                    scheduled_at = %s,
                    updated_at = now(),
                    worker_id = null,
                    lease_expires_at = null
                where run_id = %s
                returning *
                """,
                (
                    RunStatus.QUEUED.value,
                    error,
                    scheduled_at,
                    run_id,
                ),
            ).fetchone()
            if row is None:
                raise KeyError(f"Run {run_id} does not exist.")
            self._insert_event(
                connection,
                run_id,
                event_type="run.retry_scheduled",
                category="run",
                payload={
                    "error": error,
                    "scheduled_at": to_iso8601(scheduled_at),
                    "attempt_count": row["attempt_count"],
                    "max_attempts": row["max_attempts"],
                },
            )
            return self._build_run(dict(row))

    def list_events(self, run_id: str, after_sequence: int = 0) -> list[RunEvent]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                select *
                from runtime_run_events
                where run_id = %s and sequence > %s
                order by sequence asc
                """,
                (run_id, after_sequence),
            ).fetchall()
        return [self._build_event(dict(row)) for row in rows]

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
        with self._connection() as connection, connection.transaction():
            row = self._insert_event(
                connection,
                run_id,
                event_type=event_type,
                category=category,
                payload=payload,
                node_name=node_name,
                tool_name=tool_name,
                model_name=model_name,
            )
        return self._build_event(dict(row))

    def mark_run_completed(self, run_id: str, output: dict[str, Any]) -> RunRecord:
        return self._finish_run(
            run_id,
            status=RunStatus.COMPLETED,
            output=output,
            error=None,
            terminal_event_type="run.completed",
            terminal_payload=output,
        )

    def mark_run_failed(self, run_id: str, error: str) -> RunRecord:
        return self._finish_run(
            run_id,
            status=RunStatus.FAILED,
            output=None,
            error=error,
            terminal_event_type="run.failed",
            terminal_payload={"error": error},
        )

    def mark_run_cancelled(self, run_id: str, reason: str) -> RunRecord:
        return self._finish_run(
            run_id,
            status=RunStatus.CANCELLED,
            output=None,
            error=reason,
            terminal_event_type="run.cancelled",
            terminal_payload={"reason": reason},
        )

    @contextmanager
    def _connection(self) -> Any:
        connection = psycopg.connect(
            self._config.database_url,
            row_factory=dict_row,
            autocommit=False,
            connect_timeout=5,
            application_name=self._config.application_name,
        )
        try:
            yield connection
        finally:
            connection.close()

    def _apply_migration_file(self, migration_path: Path) -> None:
        sql = migration_path.read_text(encoding="utf-8")
        with self._connection() as connection, connection.transaction():
            connection.execute(
                """
                create table if not exists schema_migrations (
                    version text primary key,
                    applied_at timestamptz not null default now()
                )
                """
            )
            applied = connection.execute(
                "select 1 from schema_migrations where version = %s",
                (migration_path.name,),
            ).fetchone()
            if applied is not None:
                return
            connection.execute(sql)
            connection.execute(
                "insert into schema_migrations (version) values (%s)",
                (migration_path.name,),
            )

    def _insert_event(
        self,
        connection: Any,
        run_id: str,
        *,
        event_type: str,
        category: str,
        payload: dict[str, Any] | None = None,
        node_name: str | None = None,
        tool_name: str | None = None,
        model_name: str | None = None,
    ) -> Any:
        trace = capture_current_trace()
        run_state = connection.execute(
            """
            update runtime_runs
            set event_sequence = event_sequence + 1,
                updated_at = now()
            where run_id = %s
            returning event_sequence, trace_id
            """,
            (run_id,),
        ).fetchone()
        sequence = run_state["event_sequence"]
        trace_id = trace.trace_id or run_state["trace_id"]
        row = connection.execute(
            """
            insert into runtime_run_events (
                event_id,
                run_id,
                sequence,
                event_type,
                category,
                node_name,
                tool_name,
                model_name,
                trace_id,
                span_id,
                parent_span_id,
                payload
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            returning *
            """,
            (
                f"evt_{uuid.uuid4().hex}",
                run_id,
                sequence,
                event_type,
                category,
                node_name,
                tool_name,
                model_name,
                trace_id,
                trace.span_id,
                trace.parent_span_id,
                json.dumps(payload or {}),
            ),
        ).fetchone()
        usage = self._extract_usage(payload or {})
        if usage is not None:
            connection.execute(
                """
                insert into runtime_run_usage (
                    usage_id,
                    run_id,
                    event_id,
                    model_name,
                    input_tokens,
                    output_tokens,
                    total_tokens
                )
                values (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    f"usage_{uuid.uuid4().hex}",
                    run_id,
                    row["event_id"],
                    model_name,
                    usage.input_tokens,
                    usage.output_tokens,
                    usage.total_tokens,
                ),
            )
        return row

    def _finish_run(
        self,
        run_id: str,
        *,
        status: RunStatus,
        output: dict[str, Any] | None,
        error: str | None,
        terminal_event_type: str,
        terminal_payload: dict[str, Any],
    ) -> RunRecord:
        with self._connection() as connection, connection.transaction():
            row = connection.execute(
                """
                update runtime_runs
                set status = %s,
                    output_payload = %s::jsonb,
                    error_text = %s,
                    completed_at = now(),
                    updated_at = now(),
                    lease_expires_at = null
                where run_id = %s
                returning *
                """,
                (
                    status.value,
                    json.dumps(output),
                    error,
                    run_id,
                ),
            ).fetchone()
            self._insert_event(
                connection,
                run_id,
                event_type=terminal_event_type,
                category="run",
                payload=terminal_payload,
            )
            return self._build_run(dict(row))

    def _extract_usage(self, payload: dict[str, Any]) -> TokenUsage | None:
        usage = payload.get("usage")
        if not isinstance(usage, dict):
            return None
        return TokenUsage.model_validate(usage)

    def _build_run(self, row: dict[str, Any]) -> RunRecord:
        return RunRecord(
            run_id=row["run_id"],
            workflow=row["workflow_name"],
            status=RunStatus(row["status"]),
            input=row["input_text"],
            metadata=row["metadata"] or {},
            output=row["output_payload"],
            error=row["error_text"],
            scheduled_at=to_iso8601(row["scheduled_at"]) or "",
            created_at=to_iso8601(row["created_at"]) or "",
            updated_at=to_iso8601(row["updated_at"]) or "",
            started_at=to_iso8601(row["started_at"]),
            completed_at=to_iso8601(row["completed_at"]),
            cancel_requested_at=to_iso8601(row["cancel_requested_at"]),
            attempt_count=row["attempt_count"],
            max_attempts=row["max_attempts"],
            timeout_seconds=row["timeout_seconds"],
            worker_id=row["worker_id"],
            lease_expires_at=to_iso8601(row["lease_expires_at"]),
            trace_id=row["trace_id"],
            traceparent=row["traceparent"],
        )

    def _build_event(self, row: dict[str, Any]) -> RunEvent:
        return RunEvent(
            event_id=row["event_id"],
            run_id=row["run_id"],
            sequence=row["sequence"],
            event_type=row["event_type"],
            category=row["category"],
            created_at=to_iso8601(row["created_at"]) or "",
            node_name=row["node_name"],
            tool_name=row["tool_name"],
            model_name=row["model_name"],
            trace_id=row["trace_id"],
            span_id=row["span_id"],
            parent_span_id=row["parent_span_id"],
            payload=row["payload"] or {},
        )
