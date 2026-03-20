from __future__ import annotations

import time

from agent_harness_contracts import TokenUsage

from agent_harness_core.workflows.types import WorkflowDefinition, WorkflowResponse


def normalize_whitespace(user_input: str) -> str:
    return " ".join(user_input.strip().split())


def create_demo_echo_workflow() -> WorkflowDefinition:
    def generate_response(normalized_input: str) -> WorkflowResponse:
        started = time.perf_counter()
        response = f"Echo: {normalized_input}"
        input_tokens = len(normalized_input.split())
        output_tokens = len(response.split())
        latency_ms = int((time.perf_counter() - started) * 1000)
        return WorkflowResponse(
            response=response,
            model_name="demo-echo-model",
            usage=TokenUsage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=input_tokens + output_tokens,
            ),
            latency_ms=latency_ms,
            request_id="demo-echo",
        )

    return WorkflowDefinition(
        name="demo.echo",
        normalize_input=normalize_whitespace,
        generate_response=generate_response,
        model_name_hint="demo-echo-model",
    )
