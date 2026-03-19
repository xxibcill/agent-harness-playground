from __future__ import annotations

import json

from fastapi.testclient import TestClient

from agent_harness_contracts import CreateRunRequest, RunStatus
from agent_harness_core import InMemoryRunStore, RuntimeExecutor
from agent_harness_api.main import create_app
from agent_harness_worker.main import WorkerConfig, run_once


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
