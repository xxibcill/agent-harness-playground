from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

TERMINAL_RUN_STATUSES = {"completed", "failed", "cancelled"}


def _env_text(name: str, default: str) -> str:
    value = os.getenv(name, default).strip()
    return value or default


def _env_float(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


def _load_optional_json_env(name: str) -> dict[str, Any] | None:
    raw_value = os.getenv(name, "").strip()
    if not raw_value:
        return None

    loaded = json.loads(raw_value)
    if not isinstance(loaded, dict):
        raise ValueError(f"{name} must contain a JSON object.")
    return loaded


@dataclass(frozen=True)
class CanaryConfig:
    api_base_url: str
    poll_interval_seconds: float
    proxy_role: str
    proxy_role_header: str
    proxy_secret: str | None
    proxy_secret_header: str
    proxy_user: str
    proxy_user_header: str
    run_input: str
    timeout_seconds: float
    web_base_url: str
    worker_health_url: str
    workflow: str
    workflow_config: dict[str, Any] | None

    @classmethod
    def from_env(cls) -> "CanaryConfig":
        workflow = _env_text("AGENT_HARNESS_CANARY_WORKFLOW", "demo.echo")
        default_input = f"production canary {datetime.now(tz=UTC).isoformat()}"
        proxy_secret = os.getenv("AGENT_HARNESS_CANARY_PROXY_SECRET", "").strip() or None
        return cls(
            api_base_url=_env_text("AGENT_HARNESS_CANARY_API_BASE_URL", "http://127.0.0.1:8000"),
            poll_interval_seconds=_env_float("AGENT_HARNESS_CANARY_POLL_SECONDS", 2.0),
            proxy_role=_env_text("AGENT_HARNESS_CANARY_ROLE", "operator"),
            proxy_role_header=_env_text(
                "AGENT_HARNESS_CANARY_ROLE_HEADER",
                "x-forwarded-role",
            ),
            proxy_secret=proxy_secret,
            proxy_secret_header=_env_text(
                "AGENT_HARNESS_CANARY_PROXY_SECRET_HEADER",
                "x-agent-harness-proxy-secret",
            ),
            proxy_user=_env_text("AGENT_HARNESS_CANARY_USER", "production-canary"),
            proxy_user_header=_env_text(
                "AGENT_HARNESS_CANARY_USER_HEADER",
                "x-forwarded-user",
            ),
            run_input=_env_text("AGENT_HARNESS_CANARY_INPUT", default_input),
            timeout_seconds=_env_float("AGENT_HARNESS_CANARY_TIMEOUT_SECONDS", 90.0),
            web_base_url=_env_text("AGENT_HARNESS_CANARY_WEB_BASE_URL", "http://127.0.0.1:3000"),
            worker_health_url=_env_text(
                "AGENT_HARNESS_CANARY_WORKER_HEALTH_URL",
                "http://127.0.0.1:9102/health",
            ),
            workflow=workflow,
            workflow_config=_load_optional_json_env(
                "AGENT_HARNESS_CANARY_WORKFLOW_CONFIG_JSON"
            ),
        )

    def web_headers(self, *, accept: str = "application/json") -> dict[str, str]:
        headers = {"accept": accept}
        if self.proxy_secret is None:
            return headers

        headers[self.proxy_secret_header] = self.proxy_secret
        headers[self.proxy_user_header] = self.proxy_user
        headers[self.proxy_role_header] = self.proxy_role
        return headers

    def web_url(self, path: str) -> str:
        return f"{self.web_base_url.rstrip('/')}{path}"


def expect_json(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    expected_status: int,
    headers: dict[str, str] | None = None,
    json_body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    response = client.request(method, url, headers=headers, json=json_body)
    if response.status_code != expected_status:
        raise RuntimeError(
            f"{method} {url} returned {response.status_code}: {response.text.strip()}"
        )
    return response.json()


def verify_api_health(client: httpx.Client, config: CanaryConfig) -> None:
    payload = expect_json(
        client,
        "GET",
        f"{config.api_base_url.rstrip('/')}/health",
        expected_status=200,
    )
    if payload.get("status") != "ok":
        raise RuntimeError(f"API health check failed: {payload}")
    print(f"API healthy at {config.api_base_url}")


def verify_worker_health(client: httpx.Client, config: CanaryConfig) -> None:
    payload = expect_json(
        client,
        "GET",
        config.worker_health_url,
        expected_status=200,
    )
    if payload.get("status") != "ok":
        raise RuntimeError(f"Worker health check failed: {payload}")
    print(
        "Worker healthy at"
        f" {config.worker_health_url} worker_id={payload.get('worker_id')}"
        f" current_run_id={payload.get('current_run_id')}"
    )


def create_canary_run(client: httpx.Client, config: CanaryConfig) -> str:
    payload: dict[str, Any] = {
        "workflow": config.workflow,
        "input": config.run_input,
        "metadata": {
            "origin": "production-canary",
            "requested_at": datetime.now(tz=UTC).isoformat(),
        },
    }
    if config.workflow_config is not None:
        payload["workflow_config"] = config.workflow_config

    response = client.post(
        config.web_url("/api/runs"),
        headers=config.web_headers(),
        json=payload,
    )
    if response.status_code != 202:
        raise RuntimeError(
            "Web canary submission failed via same-origin /api/runs: "
            f"{response.status_code} {response.text.strip()}"
        )

    run_id = response.json()["run"]["run_id"]
    print(f"Created canary run through web proxy: {run_id}")
    return run_id


def wait_for_run_completion(
    client: httpx.Client,
    config: CanaryConfig,
    run_id: str,
) -> dict[str, Any]:
    deadline = time.monotonic() + config.timeout_seconds
    last_status = "queued"

    while time.monotonic() < deadline:
        payload = expect_json(
            client,
            "GET",
            config.web_url(f"/api/runs/{run_id}"),
            expected_status=200,
            headers=config.web_headers(),
        )
        run = payload["run"]
        status = str(run["status"])
        if status != last_status:
            print(f"Run {run_id} status={status}")
            last_status = status
        if status in TERMINAL_RUN_STATUSES:
            return run
        time.sleep(config.poll_interval_seconds)

    raise TimeoutError(
        f"Run {run_id} did not reach a terminal state within {config.timeout_seconds} seconds."
    )


def parse_event_stream(document: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for block in document.split("\n\n"):
        data_lines = [
            line[len("data: ") :]
            for line in block.splitlines()
            if line.startswith("data: ")
        ]
        if not data_lines:
            continue
        events.append(json.loads("\n".join(data_lines)))
    return events


def fetch_run_events(
    client: httpx.Client,
    config: CanaryConfig,
    run_id: str,
) -> list[dict[str, Any]]:
    response = client.get(
        config.web_url(f"/api/runs/{run_id}/events/stream?follow=false"),
        headers=config.web_headers(accept="text/event-stream"),
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"Run event fetch failed for {run_id}: {response.status_code} {response.text.strip()}"
        )
    return parse_event_stream(response.text)


def validate_terminal_run(run: dict[str, Any], events: list[dict[str, Any]]) -> None:
    status = str(run["status"])
    event_types = [str(event["event_type"]) for event in events]
    required_events = {"run.started", "run.completed"}
    missing_events = sorted(required_events.difference(event_types))

    if status != "completed":
        raise RuntimeError(
            f"Run finished with status={status}. Recent events: {event_types[-6:]}"
        )
    if missing_events:
        raise RuntimeError(
            f"Run completed but was missing expected events {missing_events}. "
            f"Observed events: {event_types}"
        )

    print(f"Run {run['run_id']} completed with required events present.")


def main() -> int:
    config = CanaryConfig.from_env()
    with httpx.Client(timeout=10.0, follow_redirects=True) as client:
        verify_api_health(client, config)
        verify_worker_health(client, config)
        run_id = create_canary_run(client, config)
        run = wait_for_run_completion(client, config, run_id)
        events = fetch_run_events(client, config, run_id)
        validate_terminal_run(run, events)
        verify_worker_health(client, config)

    print("Production canary passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
