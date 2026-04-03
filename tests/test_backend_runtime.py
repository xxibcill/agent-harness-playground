from __future__ import annotations

import json
from datetime import timedelta
from typing import Any, cast
from urllib.request import urlopen

import pytest
from agent_harness_api.auth import Role, TokenAuthorizer
from agent_harness_api.main import create_app
from agent_harness_contracts import (
    CreateRunRequest,
    RunStatus,
    WorkflowConfig,
    WorkflowProvider,
    WorkflowRuntimeOverrides,
)
from agent_harness_core import InMemoryRunStore, RuntimeExecutor
from agent_harness_core.executor import ExecutionTimedOut, ProviderError
from agent_harness_core.runtime import utc_now
from agent_harness_core.workflows import (
    UnknownWorkflowError,
    WorkflowDefinition,
    WorkflowRegistry,
    build_anthropic_workflow,
)
from agent_harness_core.workflows.demo_echo import create_demo_echo_workflow, normalize_whitespace
from agent_harness_core.workflows.demo_react_once import create_demo_react_once_workflow
from agent_harness_core.workflows.demo_route import create_demo_route_workflow
from agent_harness_core.workflows.demo_tool_select import create_demo_tool_select_workflow
from agent_harness_core.workflows.demo_tool_single import create_demo_tool_single_workflow
from agent_harness_observability import build_observability
from agent_harness_worker.main import (
    WorkerConfig,
    WorkerHealthMonitor,
    WorkerHealthServer,
    run_once,
)
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter


def require_model_output(output: dict[str, Any] | None) -> dict[str, Any]:
    assert output is not None
    model_output = output.get("model")
    assert isinstance(model_output, dict)
    return cast(dict[str, Any], model_output)


def test_api_creates_lists_gets_and_streams_run_events() -> None:
    store = InMemoryRunStore()
    client = TestClient(create_app(store=store))

    create_response = client.post(
        "/runs",
        json={
            "workflow": "demo.echo",
            "input": "Hello runtime",
            "metadata": {"origin": "test"},
            "workflow_config": {
                "provider": "anthropic",
                "model": "provider-model",
                "max_tokens": 256,
                "runtime_overrides": {
                    "base_url": "https://example.invalid/anthropic",
                    "client_timeout_seconds": 45,
                },
            },
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
    assert get_response.json()["run"]["workflow_config"] == {
        "provider": "anthropic",
        "model": "provider-model",
        "max_tokens": 256,
        "runtime_overrides": {
            "base_url": "https://example.invalid/anthropic",
            "client_timeout_seconds": 45,
        },
    }

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


def test_api_defaults_workflow_config_for_backwards_compatible_requests() -> None:
    store = InMemoryRunStore()
    client = TestClient(create_app(store=store))

    response = client.post(
        "/runs",
        json={
            "workflow": "demo.echo",
            "input": "backwards compatible",
        },
    )

    assert response.status_code == 202
    assert response.json()["run"]["workflow_config"] == {
        "provider": None,
        "model": None,
        "max_tokens": None,
        "runtime_overrides": None,
    }


def test_api_rejects_invalid_workflow_config_payloads() -> None:
    store = InMemoryRunStore()
    client = TestClient(create_app(store=store))

    response = client.post(
        "/runs",
        json={
            "workflow": "anthropic.respond",
            "input": "invalid config",
            "workflow_config": {
                "provider": "anthropic",
                "max_tokens": 0,
            },
        },
    )

    assert response.status_code == 422


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
    model_output = require_model_output(completed.output)
    assert completed.output == {
        "response": "Echo: hello worker",
        "normalized_input": "hello worker",
        "model": {
            "provider": "demo",
            "model_name": "demo-echo-model",
            "usage": {
                "input_tokens": 2,
                "output_tokens": 3,
                "total_tokens": 5,
            },
            "latency_ms": model_output["latency_ms"],
            "request_id": "demo-echo",
        },
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

    events_by_type = {event.event_type: event for event in store.list_events(run.run_id)}

    assert events_by_type["workflow.started"].payload == {
        "workflow": "demo.echo",
        "attempt_count": 1,
        "max_attempts": 3,
        "timeout_seconds": 300,
        "input_summary": {
            "preview": "  hello    worker  ",
            "chars": 19,
            "words": 2,
            "truncated": False,
        },
        "workflow_config": {},
    }
    assert events_by_type["node.started"].payload["state_summary"]["user_input"] == {
        "preview": "  hello    worker  ",
        "chars": 19,
        "words": 2,
        "truncated": False,
    }
    assert events_by_type["tool.completed"].payload == {
        "normalized_input": "hello worker",
        "input_summary": {
            "preview": "  hello    worker  ",
            "chars": 19,
            "words": 2,
            "truncated": False,
        },
        "output_summary": {
            "preview": "hello worker",
            "chars": 12,
            "words": 2,
            "truncated": False,
        },
        "changed": True,
    }
    assert events_by_type["model.started"].payload == {
        "input": "hello worker",
        "input_summary": {
            "preview": "hello worker",
            "chars": 12,
            "words": 2,
            "truncated": False,
        },
        "provider": "demo",
        "attempt_count": 1,
    }
    assert events_by_type["model.completed"].payload["response_summary"] == {
        "preview": "Echo: hello worker",
        "chars": 18,
        "words": 3,
        "truncated": False,
    }
    assert events_by_type["workflow.completed"].payload["state_summary"]["response"] == {
        "preview": "Echo: hello worker",
        "chars": 18,
        "words": 3,
        "truncated": False,
    }

    node_completed_events = [
        event
        for event in store.list_events(run.run_id)
        if event.event_type == "node.completed"
    ]
    assert node_completed_events[0].payload["state_diff"] == {
        "added": [],
        "removed": [],
        "updated": ["normalized_input"],
    }
    assert node_completed_events[1].payload["state_diff"] == {
        "added": [],
        "removed": [],
        "updated": ["model_output", "response"],
    }


def test_worker_executes_basic_react_workflow_with_tool_loop() -> None:
    store = InMemoryRunStore()
    run = store.create_run(
        CreateRunRequest(
            workflow="demo.react",
            input="calculate 2 + 3 * 4",
        )
    )

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
        "response": "I used calculator on 2 + 3 * 4 and got 14.",
        "normalized_input": "calculate 2 + 3 * 4",
        "tool_output": "14",
        "thought": "I observed the tool result and can answer now.",
        "tool_history": [
            {
                "tool_name": "calculator",
                "tool_input": "2 + 3 * 4",
                "tool_output": "14",
            }
        ],
        "tool_calls": 1,
    }

    events = store.list_events(run.run_id)
    event_types = [event.event_type for event in events]
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
        "node.completed",
        "node.started",
        "tool.started",
        "tool.completed",
        "node.completed",
        "node.started",
        "node.completed",
        "node.started",
        "node.completed",
        "workflow.completed",
        "run.completed",
    ]

    reason_events = [
        event
        for event in events
        if event.event_type == "node.completed" and event.node_name == "reason"
    ]
    assert len(reason_events) == 2
    assert reason_events[0].payload["selected_tool"] == "calculator"
    assert reason_events[0].payload["tool_input"] == "2 + 3 * 4"
    assert reason_events[1].payload["thought"] == "I observed the tool result and can answer now."

    use_tool_started = next(
        event
        for event in events
        if event.event_type == "tool.started" and event.node_name == "use_tool"
    )
    assert use_tool_started.tool_name == "calculator"
    assert use_tool_started.payload["input_summary"] == {
        "preview": "2 + 3 * 4",
        "chars": 9,
        "words": 5,
        "truncated": False,
    }

    use_tool_completed = next(
        event
        for event in events
        if event.event_type == "tool.completed" and event.node_name == "use_tool"
    )
    assert use_tool_completed.tool_name == "calculator"
    assert use_tool_completed.payload["output_summary"] == {
        "preview": "14",
        "chars": 2,
        "words": 1,
        "truncated": False,
    }


def test_worker_health_endpoint_reports_liveness_and_recent_completion() -> None:
    store = InMemoryRunStore()
    run = store.create_run(CreateRunRequest(input="health check"))
    config = WorkerConfig(
        poll_interval_seconds=0.0,
        lease_seconds=30,
        worker_id="worker-health",
        health_host="127.0.0.1",
        health_port=0,
        health_stale_after_seconds=60.0,
    )
    health_monitor = WorkerHealthMonitor(config)
    health_server = WorkerHealthServer(config, health_monitor)
    health_server.start()

    try:
        did_work = run_once(
            store,
            RuntimeExecutor(store),
            config,
            health_monitor=health_monitor,
        )

        assert did_work is True

        with urlopen(f"http://127.0.0.1:{health_server.port}/health", timeout=5) as response:
            payload = json.load(response)

        assert payload["service"] == "worker"
        assert payload["status"] == "ok"
        assert payload["worker_id"] == "worker-health"
        assert payload["current_run_id"] is None
        assert payload["last_finished_run_id"] == run.run_id
        assert payload["last_finished_at"] is not None
        assert payload["seconds_since_heartbeat"] >= 0
    finally:
        health_server.stop()


def test_workflow_registry_raises_for_unknown_workflows() -> None:
    registry = WorkflowRegistry({"demo.echo": create_demo_echo_workflow()})

    with pytest.raises(UnknownWorkflowError, match="missing.workflow"):
        registry.get("missing.workflow")


def test_worker_executes_anthropic_workflow_from_registry(monkeypatch) -> None:
    captured_client_config: dict[str, object] = {}
    captured_request: dict[str, object] = {}

    class FakeMessages:
        def create(self, **kwargs: object):
            captured_request.update(kwargs)
            return type(
                "Response",
                (),
                {
                    "content": [
                        type("Block", (), {"type": "text", "text": "Shared model reply"})()
                    ],
                    "usage": type(
                        "Usage",
                        (),
                        {
                            "input_tokens": 4,
                            "output_tokens": 3,
                            "cache_creation_input_tokens": 0,
                            "cache_read_input_tokens": 0,
                        },
                    )(),
                    "_request_id": "req_worker_1",
                },
            )()

    class FakeClient:
        def __init__(self, config) -> None:
            captured_client_config.update(
                {
                    "model": config.model,
                    "base_url": config.base_url,
                    "timeout_seconds": config.timeout_seconds,
                    "max_tokens": config.max_tokens,
                }
            )
            self.messages = FakeMessages()

    monkeypatch.setattr(
        "agent_harness_core.workflows.anthropic.create_client",
        lambda config: FakeClient(config),
    )
    monkeypatch.setattr(
        "agent_harness_core.workflows.anthropic.append_usage_entry",
        lambda _entry: None,
    )
    monkeypatch.setattr(
        "agent_harness_core.workflows.anthropic.read_usage_entries",
        lambda: [],
    )

    store = InMemoryRunStore()
    run = store.create_run(
        CreateRunRequest(
            workflow="anthropic.respond",
            input="  hello    worker  ",
            workflow_config=WorkflowConfig(
                provider=WorkflowProvider.ANTHROPIC,
                model="run-selected-model",
                max_tokens=321,
                runtime_overrides=WorkflowRuntimeOverrides(
                    base_url="https://example.invalid/anthropic",
                    client_timeout_seconds=12,
                ),
            ),
        )
    )
    registry = WorkflowRegistry(
        {
            "demo.echo": create_demo_echo_workflow(),
            "anthropic.respond": build_anthropic_workflow,
        }
    )

    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "test-token")
    monkeypatch.setenv("ANTHROPIC_MODEL", "env-model")
    monkeypatch.setenv("ANTHROPIC_MAX_TOKENS", "128")
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    monkeypatch.setenv("API_TIMEOUT_MS", "30000")

    did_work = run_once(
        store,
        RuntimeExecutor(store, workflow_registry=registry),
        WorkerConfig(poll_interval_seconds=0.0, lease_seconds=30, worker_id="worker-test"),
    )

    assert did_work is True

    completed = store.get_run(run.run_id)
    assert completed is not None
    assert completed.status == RunStatus.COMPLETED
    model_output = require_model_output(completed.output)
    assert completed.output == {
        "response": "Shared model reply",
        "normalized_input": "hello worker",
        "model": {
            "provider": "anthropic",
            "model_name": "run-selected-model",
            "usage": {
                "input_tokens": 4,
                "output_tokens": 3,
                "total_tokens": 7,
            },
            "latency_ms": model_output["latency_ms"],
            "request_id": "req_worker_1",
        },
    }
    assert captured_client_config == {
        "model": "run-selected-model",
        "base_url": "https://example.invalid/anthropic",
        "timeout_seconds": 12.0,
        "max_tokens": 321,
    }
    assert captured_request == {
        "model": "run-selected-model",
        "max_tokens": 321,
        "messages": [{"role": "user", "content": "hello worker"}],
    }


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


def test_end_to_end_web_submission_through_worker_completion(monkeypatch) -> None:
    captured_request: dict[str, object] = {}

    class FakeMessages:
        def create(self, **kwargs: object):
            captured_request.update(kwargs)
            return type(
                "Response",
                (),
                {
                    "content": [
                        type("Block", (), {"type": "text", "text": "Model-backed reply"})()
                    ],
                    "usage": type(
                        "Usage",
                        (),
                        {
                            "input_tokens": 6,
                            "output_tokens": 4,
                            "cache_creation_input_tokens": 0,
                            "cache_read_input_tokens": 0,
                        },
                    )(),
                    "_request_id": "req_e2e_1",
                },
            )()

    class FakeClient:
        def __init__(self, _config) -> None:
            self.messages = FakeMessages()

    monkeypatch.setattr(
        "agent_harness_core.workflows.anthropic.load_project_env",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "agent_harness_core.workflows.anthropic.create_client",
        lambda config: FakeClient(config),
    )
    monkeypatch.setattr(
        "agent_harness_core.workflows.anthropic.append_usage_entry",
        lambda _entry: None,
    )
    monkeypatch.setattr(
        "agent_harness_core.workflows.anthropic.read_usage_entries",
        lambda: [],
    )
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "test-token")
    monkeypatch.setenv("API_TIMEOUT_MS", "30000")

    store = InMemoryRunStore()
    client = TestClient(create_app(store=store))
    registry = WorkflowRegistry(
        {
            "demo.echo": create_demo_echo_workflow(),
            "anthropic.respond": build_anthropic_workflow,
        }
    )

    create_response = client.post(
        "/runs",
        json={
            "workflow": "anthropic.respond",
            "input": "Test end-to-end execution",
            "metadata": {"origin": "e2e-test", "user": "test-operator"},
            "workflow_config": {
                "provider": "anthropic",
                "model": "claude-test-model",
                "max_tokens": 128,
            },
            "max_attempts": 1,
            "timeout_seconds": 60,
        },
    )
    assert create_response.status_code == 202
    run_id = create_response.json()["run"]["run_id"]

    did_work = run_once(
        store,
        RuntimeExecutor(store, workflow_registry=registry),
        WorkerConfig(poll_interval_seconds=0.0, lease_seconds=30, worker_id="worker-test"),
    )

    assert did_work is True
    completed = store.get_run(run_id)
    assert completed is not None
    assert completed.status == RunStatus.COMPLETED
    model_output = require_model_output(completed.output)
    assert completed.output == {
        "response": "Model-backed reply",
        "normalized_input": "Test end-to-end execution",
        "model": {
            "provider": "anthropic",
            "model_name": "claude-test-model",
            "usage": {
                "input_tokens": 6,
                "output_tokens": 4,
                "total_tokens": 10,
            },
            "latency_ms": model_output["latency_ms"],
            "request_id": "req_e2e_1",
        },
    }
    assert captured_request == {
        "model": "claude-test-model",
        "max_tokens": 128,
        "messages": [{"role": "user", "content": "Test end-to-end execution"}],
    }

    get_response = client.get(f"/runs/{run_id}")
    assert get_response.status_code == 200
    assert get_response.json()["run"]["status"] == RunStatus.COMPLETED.value

    stream_response = client.get(
        f"/runs/{run_id}/events/stream",
        params={"follow": "false"},
    )
    assert stream_response.status_code == 200
    assert '"event_type": "model.started"' in stream_response.text
    assert '"event_type": "model.completed"' in stream_response.text


def test_demo_workflow_persists_structured_model_output_for_operator_ui() -> None:
    store = InMemoryRunStore()
    run = store.create_run(CreateRunRequest(input="  hello   metrics  "))

    did_work = run_once(
        store,
        RuntimeExecutor(store),
        WorkerConfig(poll_interval_seconds=0.0, lease_seconds=30, worker_id="worker-test"),
    )

    assert did_work is True
    completed = store.get_run(run.run_id)
    assert completed is not None
    assert completed.status == RunStatus.COMPLETED
    model_output = require_model_output(completed.output)
    assert completed.output == {
        "response": "Echo: hello metrics",
        "normalized_input": "hello metrics",
        "model": {
            "provider": "demo",
            "model_name": "demo-echo-model",
            "usage": {
                "input_tokens": 2,
                "output_tokens": 3,
                "total_tokens": 5,
            },
            "latency_ms": model_output["latency_ms"],
            "request_id": "demo-echo",
        },
    }

    events = store.list_events(run.run_id)
    model_completed = next(event for event in events if event.event_type == "model.completed")
    assert model_completed.payload["provider"] == "demo"
    assert model_completed.payload["model_name"] == "demo-echo-model"
    assert model_completed.payload["usage"] == {
        "input_tokens": 2,
        "output_tokens": 3,
        "total_tokens": 5,
    }


def test_configuration_failure_emits_model_failed_and_marks_run_failed(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "agent_harness_core.workflows.anthropic.load_project_env",
        lambda *args, **kwargs: None,
    )
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)

    store = InMemoryRunStore()
    run = store.create_run(
        CreateRunRequest(
            workflow="anthropic.respond",
            input="configuration failure",
            workflow_config=WorkflowConfig(
                provider=WorkflowProvider.ANTHROPIC,
                model="claude-missing-token",
            ),
            max_attempts=1,
        )
    )
    registry = WorkflowRegistry(
        {
            "demo.echo": create_demo_echo_workflow(),
            "anthropic.respond": build_anthropic_workflow,
        }
    )

    did_work = run_once(
        store,
        RuntimeExecutor(store, workflow_registry=registry),
        WorkerConfig(poll_interval_seconds=0.0, lease_seconds=30, worker_id="worker-test"),
    )

    assert did_work is True
    failed = store.get_run(run.run_id)
    assert failed is not None
    assert failed.status == RunStatus.FAILED

    events = store.list_events(run.run_id)
    model_failed = next(event for event in events if event.event_type == "model.failed")
    assert model_failed.payload["error_type"] == "configuration_error"
    assert "ANTHROPIC_AUTH_TOKEN" in model_failed.payload["error_message"]
    assert model_failed.model_name == "claude-missing-token"
    assert "run.retry_scheduled" not in [event.event_type for event in events]


def test_provider_error_emits_recovery_hint_and_stops_retrying() -> None:
    def build_provider_failure_workflow(_run=None) -> WorkflowDefinition:
        def generate_response(_normalized_input: str):
            raise ProviderError(
                "Provider rejected the request.",
                error_type="authentication_error",
                recovery_hint="Rotate the API key or verify the configured model access.",
            )

        return WorkflowDefinition(
            name="anthropic.respond",
            normalize_input=normalize_whitespace,
            generate_response=generate_response,
            model_name_hint="claude-auth-failure",
        )

    store = InMemoryRunStore()
    run = store.create_run(
        CreateRunRequest(
            workflow="anthropic.respond",
            input="provider failure",
            workflow_config=WorkflowConfig(
                provider=WorkflowProvider.ANTHROPIC,
                model="claude-auth-failure",
            ),
            max_attempts=2,
        )
    )
    registry = WorkflowRegistry(
        {
            "demo.echo": create_demo_echo_workflow(),
            "anthropic.respond": build_provider_failure_workflow,
        }
    )

    did_work = run_once(
        store,
        RuntimeExecutor(store, workflow_registry=registry),
        WorkerConfig(poll_interval_seconds=0.0, lease_seconds=30, worker_id="worker-test"),
    )

    assert did_work is True
    failed = store.get_run(run.run_id)
    assert failed is not None
    assert failed.status == RunStatus.FAILED
    assert failed.attempt_count == 1

    events = store.list_events(run.run_id)
    model_failed = next(event for event in events if event.event_type == "model.failed")
    assert model_failed.payload["error_type"] == "authentication_error"
    assert (
        model_failed.payload["recovery_hint"]
        == "Rotate the API key or verify the configured model access."
    )
    assert "run.retry_scheduled" not in [event.event_type for event in events]


def test_timeout_error_emits_model_and_run_failure_events() -> None:
    class TimeoutDuringModelExecutor(RuntimeExecutor):
        def __init__(self, store: InMemoryRunStore) -> None:
            super().__init__(store)
            self._deadline_checks = 0

        def _assert_within_deadline(self, run_id: str, deadline) -> None:  # type: ignore[override,no-untyped-def]
            self._deadline_checks += 1
            if self._deadline_checks >= 12:
                raise ExecutionTimedOut(f"Run {run_id} exceeded its timeout budget.")
            super()._assert_within_deadline(run_id, deadline)

    store = InMemoryRunStore()
    run = store.create_run(
        CreateRunRequest(
            input="timeout me",
            max_attempts=1,
            timeout_seconds=5,
        )
    )

    did_work = run_once(
        store,
        TimeoutDuringModelExecutor(store),
        WorkerConfig(poll_interval_seconds=0.0, lease_seconds=30, worker_id="worker-test"),
    )

    assert did_work is True
    failed = store.get_run(run.run_id)
    assert failed is not None
    assert failed.status == RunStatus.FAILED

    event_types = [event.event_type for event in store.list_events(run.run_id)]
    assert "model.started" in event_types
    assert "model.failed" in event_types
    assert "run.timeout_exceeded" in event_types
    assert event_types[-1] == "run.failed"

    model_failed = next(
        event for event in store.list_events(run.run_id) if event.event_type == "model.failed"
    )
    assert model_failed.payload["error_type"] == "run_timeout_exceeded"


def test_event_sequence_ordering_and_consistency() -> None:
    store = InMemoryRunStore()
    run = store.create_run(CreateRunRequest(input="sequence test"))

    did_work = run_once(
        store,
        RuntimeExecutor(store),
        WorkerConfig(poll_interval_seconds=0.0, lease_seconds=30, worker_id="worker-test"),
    )

    assert did_work is True
    events = store.list_events(run.run_id)
    sequences = [event.sequence for event in events]
    assert sequences == sorted(sequences)
    assert sequences == list(range(1, len(sequences) + 1))

    event_types = [event.event_type for event in events]
    assert event_types[:2] == ["run.created", "run.queued"]
    assert event_types[-1] == "run.completed"


def test_demo_route_workflow_routes_greetings_to_greeting_response() -> None:
    store = InMemoryRunStore()
    run = store.create_run(CreateRunRequest(workflow="demo.route", input="hello there"))

    did_work = run_once(
        store,
        RuntimeExecutor(store),
        WorkerConfig(poll_interval_seconds=0.0, lease_seconds=30, worker_id="worker-test"),
    )

    assert did_work is True
    completed = store.get_run(run.run_id)
    assert completed is not None
    assert completed.status == RunStatus.COMPLETED
    assert "Hello! I'm here to help" in completed.output["response"]


def test_demo_route_workflow_routes_questions_to_question_response() -> None:
    store = InMemoryRunStore()
    run = store.create_run(CreateRunRequest(workflow="demo.route", input="what is this?"))

    did_work = run_once(
        store,
        RuntimeExecutor(store),
        WorkerConfig(poll_interval_seconds=0.0, lease_seconds=30, worker_id="worker-test"),
    )

    assert did_work is True
    completed = store.get_run(run.run_id)
    assert completed is not None
    assert completed.status == RunStatus.COMPLETED
    assert "interesting question" in completed.output["response"]


def test_demo_route_workflow_routes_commands_to_command_response() -> None:
    store = InMemoryRunStore()
    run = store.create_run(CreateRunRequest(workflow="demo.route", input="run the task"))

    did_work = run_once(
        store,
        RuntimeExecutor(store),
        WorkerConfig(poll_interval_seconds=0.0, lease_seconds=30, worker_id="worker-test"),
    )

    assert did_work is True
    completed = store.get_run(run.run_id)
    assert completed is not None
    assert completed.status == RunStatus.COMPLETED
    assert "execute that command" in completed.output["response"]


def test_demo_route_workflow_routes_plain_text_to_statement_response() -> None:
    store = InMemoryRunStore()
    run = store.create_run(CreateRunRequest(workflow="demo.route", input="the weather is nice"))

    did_work = run_once(
        store,
        RuntimeExecutor(store),
        WorkerConfig(poll_interval_seconds=0.0, lease_seconds=30, worker_id="worker-test"),
    )

    assert did_work is True
    completed = store.get_run(run.run_id)
    assert completed is not None
    assert completed.status == RunStatus.COMPLETED
    assert "I see. You said" in completed.output["response"]


def test_demo_tool_single_workflow_calls_count_words_once() -> None:
    store = InMemoryRunStore()
    run = store.create_run(
        CreateRunRequest(workflow="demo.tool.single", input="hello world from test")
    )

    did_work = run_once(
        store,
        RuntimeExecutor(store),
        WorkerConfig(poll_interval_seconds=0.0, lease_seconds=30, worker_id="worker-test"),
    )

    assert did_work is True
    completed = store.get_run(run.run_id)
    assert completed is not None
    assert completed.status == RunStatus.COMPLETED
    assert "Word count result" in completed.output["response"]
    assert "has 4 words" in completed.output["response"] or "4 words" in completed.output["response"]
    assert completed.output.get("tool_calls", 0) == 1


def test_demo_tool_select_workflow_selects_and_executes_calculator() -> None:
    store = InMemoryRunStore()
    run = store.create_run(
        CreateRunRequest(workflow="demo.tool.select", input="calculate 5 + 3")
    )

    did_work = run_once(
        store,
        RuntimeExecutor(store),
        WorkerConfig(poll_interval_seconds=0.0, lease_seconds=30, worker_id="worker-test"),
    )

    assert did_work is True
    completed = store.get_run(run.run_id)
    assert completed is not None
    assert completed.status == RunStatus.COMPLETED
    assert "calculator" in completed.output.get("response", "").lower()
    assert completed.output.get("tool_calls", 0) == 1


def test_demo_tool_select_workflow_rejects_unsupported_input() -> None:
    store = InMemoryRunStore()
    run = store.create_run(
        CreateRunRequest(workflow="demo.tool.select", input="random unsupported text")
    )

    did_work = run_once(
        store,
        RuntimeExecutor(store),
        WorkerConfig(poll_interval_seconds=0.0, lease_seconds=30, worker_id="worker-test"),
    )

    assert did_work is True
    completed = store.get_run(run.run_id)
    assert completed is not None
    assert completed.status == RunStatus.COMPLETED
    assert "No matching tool found" in completed.output["response"]


def test_demo_react_once_workflow_uses_tool_once_and_responds() -> None:
    store = InMemoryRunStore()
    run = store.create_run(
        CreateRunRequest(workflow="demo.react.once", input="calculate 10 + 5")
    )

    did_work = run_once(
        store,
        RuntimeExecutor(store),
        WorkerConfig(poll_interval_seconds=0.0, lease_seconds=30, worker_id="worker-test"),
    )

    assert did_work is True
    completed = store.get_run(run.run_id)
    assert completed is not None
    assert completed.status == RunStatus.COMPLETED
    assert "calculator" in completed.output.get("response", "").lower()
    assert completed.output.get("tool_calls", 0) == 1


def test_demo_react_once_workflow_responds_directly_when_no_tool_needed() -> None:
    store = InMemoryRunStore()
    run = store.create_run(
        CreateRunRequest(workflow="demo.react.once", input="just a plain message")
    )

    did_work = run_once(
        store,
        RuntimeExecutor(store),
        WorkerConfig(poll_interval_seconds=0.0, lease_seconds=30, worker_id="worker-test"),
    )

    assert did_work is True
    completed = store.get_run(run.run_id)
    assert completed is not None
    assert completed.status == RunStatus.COMPLETED
    assert "did not need a tool" in completed.output["response"].lower() or "Echo:" in completed.output["response"]


def test_simpler_workflow_does_not_loop_multiple_tools() -> None:
    store = InMemoryRunStore()
    run = store.create_run(CreateRunRequest(workflow="demo.echo", input="simple test"))

    did_work = run_once(
        store,
        RuntimeExecutor(store),
        WorkerConfig(poll_interval_seconds=0.0, lease_seconds=30, worker_id="worker-test"),
    )

    assert did_work is True
    completed = store.get_run(run.run_id)
    assert completed is not None
    assert completed.status == RunStatus.COMPLETED

    events = store.list_events(run.run_id)
    tool_started_events = [e for e in events if e.event_type == "tool.started"]
    assert len(tool_started_events) == 1
