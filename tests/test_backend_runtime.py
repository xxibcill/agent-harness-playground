from __future__ import annotations

import json
from datetime import timedelta

import pytest
from agent_harness_api.auth import Role, TokenAuthorizer
from agent_harness_api.main import create_app
from agent_harness_contracts import CreateRunRequest, RunStatus
from agent_harness_core import InMemoryRunStore, RuntimeExecutor
from agent_harness_core.executor import ExecutionTimedOut
from agent_harness_core.runtime import utc_now
from agent_harness_observability import build_observability
from agent_harness_worker.main import WorkerConfig, run_once
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter


def test_api_creates_lists_gets_and_streams_run_events() -> None:
    store = InMemoryRunStore()
    client = TestClient(create_app(store=store))

    create_response = client.post(
        "/runs",
        json={
            "workflow": "demo.echo",
            "input": "Hello runtime",
            "metadata": {"origin": "test"},
        },
    )

    assert create_response.status_code == 202
    run = create_response.json()["run"]
    assert run["status"] == RunStatus.QUEUED.value

    list_response = client.get("/runs")
    assert list_response.status_code == 200
    assert [item["run_id"] for item in list_response.json()["runs"]] == [run["run_id"]]

    get_response = client.get(f"/runs/{run['run_id']}")
    assert get_response.status_code == 200
    assert get_response.json()["run"]["metadata"] == {"origin": "test"}

    stream_response = client.get(
        f"/runs/{run['run_id']}/events/stream",
        params={"follow": "false"},
    )
    assert stream_response.status_code == 200
    body = stream_response.text
    assert "event: run-event" in body
    assert '"event_type": "run.created"' in body
    assert '"event_type": "run.queued"' in body


def test_trace_context_propagates_from_api_to_worker_events() -> None:
    store = InMemoryRunStore()
    api_exporter = InMemorySpanExporter()
    worker_exporter = InMemorySpanExporter()
    api_observability = build_observability(
        "agent-harness-api-test",
        span_exporter=api_exporter,
        use_batch_processor=False,
    )
    worker_observability = build_observability(
        "agent-harness-worker-test",
        span_exporter=worker_exporter,
        use_batch_processor=False,
    )
    client = TestClient(create_app(store=store, observability=api_observability))

    try:
        response = client.post(
            "/runs",
            json={
                "workflow": "demo.echo",
                "input": "trace me",
            },
        )
        assert response.status_code == 202
        run = store.get_run(response.json()["run"]["run_id"])
        assert run is not None
        assert run.trace_id is not None
        assert run.traceparent is not None

        did_work = run_once(
            store,
            RuntimeExecutor(store, observability=worker_observability),
            WorkerConfig(poll_interval_seconds=0.0, lease_seconds=30, worker_id="worker-test"),
            observability=worker_observability,
        )

        assert did_work is True
        traced_events = [
            event
            for event in store.list_events(run.run_id)
            if event.event_type
            in {"workflow.started", "node.started", "tool.started", "model.started"}
        ]
        assert traced_events
        assert all(event.trace_id == run.trace_id for event in traced_events)
        assert all(event.span_id is not None for event in traced_events)
        assert worker_exporter.get_finished_spans()
        assert {span.context.trace_id for span in worker_exporter.get_finished_spans()} == {
            int(run.trace_id, 16)
        }
    finally:
        client.close()
        worker_observability.shutdown()


def test_worker_executes_queued_runs_and_persists_runtime_events() -> None:
    store = InMemoryRunStore()
    run = store.create_run(CreateRunRequest(input="  hello    worker  "))

    did_work = run_once(
        store,
        RuntimeExecutor(store),
        WorkerConfig(poll_interval_seconds=0.0, lease_seconds=30, worker_id="worker-test"),
    )

    assert did_work is True

    completed = store.get_run(run.run_id)
    assert completed is not None
    assert completed.status == RunStatus.COMPLETED
    assert completed.output == {
        "response": "Echo: hello worker",
        "normalized_input": "hello worker",
    }

    event_types = [event.event_type for event in store.list_events(run.run_id)]
    assert event_types == [
        "run.created",
        "run.queued",
        "run.started",
        "workflow.started",
        "node.started",
        "tool.started",
        "tool.completed",
        "node.completed",
        "node.started",
        "model.started",
        "model.completed",
        "node.completed",
        "workflow.completed",
        "run.completed",
    ]


def test_worker_turns_cancelling_runs_into_cancelled_runs() -> None:
    store = InMemoryRunStore()
    run = store.create_run(CreateRunRequest(input="cancel me"))

    class CancellingExecutor(RuntimeExecutor):
        def execute(self, current_run):  # type: ignore[override]
            store.cancel_run(current_run.run_id)
            return super().execute(current_run)

    did_work = run_once(
        store,
        CancellingExecutor(store),
        WorkerConfig(poll_interval_seconds=0.0, lease_seconds=30, worker_id="worker-test"),
    )

    assert did_work is True
    final_run = store.get_run(run.run_id)
    assert final_run is not None
    assert final_run.status == RunStatus.CANCELLED


def test_metrics_endpoint_exposes_run_and_queue_metrics() -> None:
    store = InMemoryRunStore()
    observability = build_observability("agent-harness-api-metrics-test")
    client = TestClient(create_app(store=store, observability=observability))

    try:
        create_response = client.post(
            "/runs",
            json={
                "workflow": "demo.echo",
                "input": "measure me",
            },
        )
        assert create_response.status_code == 202

        metrics_response = client.get("/metrics")

        assert metrics_response.status_code == 200
        assert "agent_harness_runs_created_total" in metrics_response.text
        assert "agent_harness_queue_depth" in metrics_response.text
        assert 'workflow="demo.echo"' in metrics_response.text
    finally:
        client.close()


def test_api_cancel_endpoint_marks_queued_run_as_cancelled() -> None:
    store = InMemoryRunStore()
    client = TestClient(create_app(store=store))
    run = store.create_run(CreateRunRequest(input="please cancel"))

    cancel_response = client.post(f"/runs/{run.run_id}/cancel")

    assert cancel_response.status_code == 200
    assert cancel_response.json()["run"]["status"] == RunStatus.CANCELLED.value

    events = [event.model_dump(mode="json") for event in store.list_events(run.run_id)]
    serialized = json.dumps(events)
    assert "run.cancelled" in serialized


def test_api_allows_local_nextjs_client_origin() -> None:
    client = TestClient(create_app(store=InMemoryRunStore()))

    response = client.options(
        "/runs",
        headers={
            "Origin": "http://127.0.0.1:3000",
            "Access-Control-Request-Method": "POST",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:3000"


def test_retryable_failures_are_requeued_then_completed() -> None:
    store = InMemoryRunStore()
    run = store.create_run(CreateRunRequest(input="retry me", max_attempts=2, timeout_seconds=30))

    class FlakyExecutor(RuntimeExecutor):
        def execute(self, current_run):  # type: ignore[override]
            if current_run.attempt_count == 1:
                raise RuntimeError("temporary failure")
            return super().execute(current_run)

    config = WorkerConfig(
        poll_interval_seconds=0.0,
        lease_seconds=30,
        lease_refresh_seconds=0.01,
        retry_backoff_seconds=0,
        worker_id="worker-test",
    )

    first_attempt = run_once(store, FlakyExecutor(store), config)
    assert first_attempt is True

    requeued = store.get_run(run.run_id)
    assert requeued is not None
    assert requeued.status == RunStatus.QUEUED
    assert requeued.attempt_count == 1
    assert requeued.error == "temporary failure"

    second_attempt = run_once(store, FlakyExecutor(store), config)
    assert second_attempt is True

    completed = store.get_run(run.run_id)
    assert completed is not None
    assert completed.output is not None
    assert completed.status == RunStatus.COMPLETED
    assert completed.attempt_count == 2
    assert completed.output["response"] == "Echo: retry me"

    event_types = [event.event_type for event in store.list_events(run.run_id)]
    assert "run.execution_failed" in event_types
    assert "run.retry_scheduled" in event_types


def test_executor_stops_runs_that_exceed_timeout_budget() -> None:
    store = InMemoryRunStore()
    store.create_run(CreateRunRequest(input="late input", timeout_seconds=5))
    claimed = store.claim_next_run("worker-test", 30)

    assert claimed is not None

    expired = claimed.model_copy(
        update={"started_at": (utc_now() - timedelta(seconds=10)).isoformat()}
    )

    with pytest.raises(ExecutionTimedOut):
        RuntimeExecutor(store).execute(expired)


def test_api_enforces_roles_for_operator_and_admin_routes() -> None:
    authorizer = TokenAuthorizer(
        {
            "viewer-token": Role.VIEWER,
            "operator-token": Role.OPERATOR,
            "admin-token": Role.ADMIN,
        }
    )
    store = InMemoryRunStore()
    client = TestClient(create_app(store=store, authorizer=authorizer))

    unauthenticated = client.get("/runs")
    assert unauthenticated.status_code == 401

    viewer_list = client.get("/runs", headers={"Authorization": "Bearer viewer-token"})
    assert viewer_list.status_code == 200

    viewer_create = client.post(
        "/runs",
        headers={"Authorization": "Bearer viewer-token"},
        json={"workflow": "demo.echo", "input": "blocked"},
    )
    assert viewer_create.status_code == 403

    operator_create = client.post(
        "/runs",
        headers={"Authorization": "Bearer operator-token"},
        json={"workflow": "demo.echo", "input": "allowed"},
    )
    assert operator_create.status_code == 202
    run_id = operator_create.json()["run"]["run_id"]

    viewer_stream = client.get(f"/runs/{run_id}/events/stream?follow=false&api_token=viewer-token")
    assert viewer_stream.status_code == 200

    viewer_metrics = client.get("/metrics", headers={"Authorization": "Bearer viewer-token"})
    assert viewer_metrics.status_code == 403

    admin_metrics = client.get("/metrics", headers={"Authorization": "Bearer admin-token"})
    assert admin_metrics.status_code == 200
