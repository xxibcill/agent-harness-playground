from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from agent_harness_contracts import TokenUsage
from typing_extensions import TypedDict


class WorkflowState(TypedDict, total=False):
    user_input: str
    normalized_input: str
    response: str
    model_output: dict[str, Any] | None
    selected_tool: str | None
    tool_input: str | None
    tool_output: str | None
    thought: str
    tool_history: list[dict[str, str]]
    tool_calls: int


def build_default_workflow_state(user_input: str) -> WorkflowState:
    return {
        "user_input": user_input,
        "normalized_input": "",
        "response": "",
        "model_output": None,
    }


@dataclass(frozen=True)
class WorkflowResponse:
    response: str
    model_name: str
    usage: TokenUsage | None = None
    latency_ms: int | None = None
    request_id: str | None = None


@dataclass(frozen=True)
class WorkflowDefinition:
    name: str
    normalize_input: Callable[[str], str]
    generate_response: Callable[[str], WorkflowResponse] | None = None
    normalize_tool_name: str = "normalize_whitespace"
    model_name_hint: str | None = None
    initial_state: Callable[[str], WorkflowState] = build_default_workflow_state
    graph_factory: Callable[[Any], Any] | None = None
