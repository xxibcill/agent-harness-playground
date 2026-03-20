from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from agent_harness_contracts import TokenUsage
from typing_extensions import TypedDict


class WorkflowState(TypedDict):
    user_input: str
    normalized_input: str
    response: str


@dataclass(frozen=True)
class WorkflowResponse:
    response: str
    model_name: str
    usage: TokenUsage | None = None


@dataclass(frozen=True)
class WorkflowDefinition:
    name: str
    generate_response: Callable[[str], WorkflowResponse]
    normalize_input: Callable[[str], str]
    normalize_tool_name: str = "normalize_whitespace"
    model_name_hint: str | None = None
