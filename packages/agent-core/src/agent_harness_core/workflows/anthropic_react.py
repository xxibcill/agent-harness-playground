from __future__ import annotations

import ast
import operator
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import anthropic
from agent_harness_contracts import RunRecord, TokenUsage, WorkflowConfig
from anthropic import Anthropic
from dotenv import load_dotenv
from langgraph.graph import END, START, StateGraph

from agent_harness_core.errors import ConfigurationError, ProviderError
from agent_harness_core.usage_tracker import (
    append_usage_entry,
    build_usage_entry,
    calculate_average_tpm,
    calculate_rolling_tpm,
    read_usage_entries,
)
from agent_harness_core.workflows.demo_echo import normalize_whitespace
from agent_harness_core.workflows.types import (
    WorkflowDefinition,
    WorkflowState,
    build_default_workflow_state,
)


PROJECT_ROOT = Path(__file__).resolve().parents[5]
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"


def load_project_env(env_file: Path = DEFAULT_ENV_FILE) -> None:
    if env_file.exists():
        load_dotenv(env_file, override=False)


@dataclass(frozen=True)
class AnthropicReactConfig:
    api_key: str
    model: str
    base_url: str | None
    timeout_seconds: float
    max_tokens: int
    max_tool_calls: int


def load_config(
    workflow_config: WorkflowConfig | str | None = None,
    *,
    model_override: str | None = None,
    max_tool_calls_override: int | None = None,
) -> AnthropicReactConfig:
    load_project_env()
    resolved_workflow_config = WorkflowConfig()
    if isinstance(workflow_config, str):
        model_override = workflow_config
    elif workflow_config is not None:
        resolved_workflow_config = workflow_config

    api_key = os.getenv("ANTHROPIC_AUTH_TOKEN") or os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ConfigurationError(
            "Set ANTHROPIC_AUTH_TOKEN or ANTHROPIC_API_KEY before running the agent."
        )

    model = model_override or resolved_workflow_config.model or os.getenv("ANTHROPIC_MODEL")
    if not model:
        raise ConfigurationError(
            "Pass --model or set ANTHROPIC_MODEL to a model ID supported by your "
            "Anthropic-compatible endpoint."
        )

    timeout_ms = os.getenv("API_TIMEOUT_MS", "600000")
    try:
        timeout_seconds = int(timeout_ms) / 1000
    except ValueError as exc:
        raise ConfigurationError(
            "API_TIMEOUT_MS must be an integer number of milliseconds."
        ) from exc

    max_tokens = resolved_workflow_config.max_tokens
    if max_tokens is None:
        max_tokens_text = os.getenv("ANTHROPIC_MAX_TOKENS", "1024")
        try:
            max_tokens = int(max_tokens_text)
        except ValueError as exc:
            raise ConfigurationError("ANTHROPIC_MAX_TOKENS must be an integer.") from exc

    max_tool_calls = max_tool_calls_override or resolved_workflow_config.max_tool_calls or 5

    base_url_override = None
    client_timeout_override = None
    if resolved_workflow_config.runtime_overrides is not None:
        base_url_override = resolved_workflow_config.runtime_overrides.base_url
        client_timeout_override = resolved_workflow_config.runtime_overrides.client_timeout_seconds

    return AnthropicReactConfig(
        api_key=api_key,
        model=model,
        base_url=base_url_override or os.getenv("ANTHROPIC_BASE_URL"),
        timeout_seconds=float(client_timeout_override or timeout_seconds),
        max_tokens=max_tokens,
        max_tool_calls=max_tool_calls,
    )


def create_client(config: AnthropicReactConfig) -> Anthropic:
    return Anthropic(
        api_key=config.api_key,
        base_url=config.base_url,
        timeout=config.timeout_seconds,
    )


import os

_CAPITALS = {
    "france": "Paris",
    "japan": "Tokyo",
    "thailand": "Bangkok",
    "united states": "Washington, D.C.",
    "germany": "Berlin",
    "italy": "Rome",
    "brazil": "Brasilia",
    "australia": "Canberra",
}
_CALCULATOR_PREFIXES = ("calculate ", "compute ", "what is ")
_WORD_COUNT_PREFIXES = ("count words in ", "count words ", "how many words are in ")
_MATH_PATTERN = re.compile(r"^[\d\s\+\-\*\/\.\(\)]+$")
_CAPITAL_PATTERN = re.compile(r"\bcapital of (?P<country>[a-zA-Z ]+?)[?.!]*$", re.IGNORECASE)
_ALLOWED_BINARY_OPERATORS: dict[type[ast.operator], Callable[[float, float], float]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
}
_ALLOWED_UNARY_OPERATORS: dict[type[ast.unaryop], Callable[[float], float]] = {
    ast.UAdd: lambda value: value,
    ast.USub: lambda value: -value,
}


def lookup_capital(country: str) -> str:
    return _CAPITALS.get(country.casefold(), f"Unknown capital for {country}")


def count_words(text: str) -> str:
    normalized = normalize_whitespace(text)
    if not normalized:
        return "0"
    return str(len(normalized.split()))


def calculate_expression(expression: str) -> str:
    parsed = ast.parse(expression, mode="eval")
    value = _evaluate_math_ast(parsed.body)
    if value.is_integer():
        return str(int(value))
    return str(value)


def _evaluate_math_ast(node: ast.AST) -> float:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.BinOp):
        operator_fn = _ALLOWED_BINARY_OPERATORS.get(type(node.op))
        if operator_fn is None:
            raise ValueError("Only +, -, *, and / are supported.")
        return operator_fn(_evaluate_math_ast(node.left), _evaluate_math_ast(node.right))
    if isinstance(node, ast.UnaryOp):
        operator_fn = _ALLOWED_UNARY_OPERATORS.get(type(node.op))
        if operator_fn is None:
            raise ValueError("Only unary + and - are supported.")
        return operator_fn(_evaluate_math_ast(node.operand))
    raise ValueError("Only basic arithmetic expressions are supported.")


def _extract_math_expression(normalized_input: str) -> str | None:
    candidate = normalized_input.strip().rstrip("?.!")
    lowered = candidate.casefold()
    for prefix in _CALCULATOR_PREFIXES:
        if lowered.startswith(prefix):
            expression = candidate[len(prefix) :].strip()
            if _looks_like_math(expression):
                return expression
    if _looks_like_math(candidate):
        return candidate
    return None


def _extract_capital_lookup_target(normalized_input: str) -> str | None:
    match = _CAPITAL_PATTERN.search(normalized_input)
    if match is None:
        return None
    return normalize_whitespace(match.group("country")).strip()


def _extract_word_count_target(normalized_input: str) -> str | None:
    lowered = normalized_input.casefold()
    for prefix in _WORD_COUNT_PREFIXES:
        if lowered.startswith(prefix):
            return normalized_input[len(prefix) :].strip(" :")
    return None


def _looks_like_math(text: str) -> bool:
    return bool(text) and _MATH_PATTERN.fullmatch(text) is not None


def _build_tool_definitions() -> list[dict]:
    return [
        {
            "name": "calculator",
            "description": "Evaluate a mathematical expression. Use for arithmetic calculations like 2+2, 10*5, etc.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "The mathematical expression to evaluate (e.g., '2 + 2', '10 * 5').",
                    }
                },
                "required": ["expression"],
            },
        },
        {
            "name": "lookup_capital",
            "description": "Look up the capital city of a country. Use when asked about a capital city.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "country": {
                        "type": "string",
                        "description": "The country name to look up (e.g., 'France', 'Japan').",
                    }
                },
                "required": ["country"],
            },
        },
        {
            "name": "count_words",
            "description": "Count the number of words in a given text. Use when asked about word count.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "The text to count words in.",
                    }
                },
                "required": ["text"],
            },
        },
    ]


def _execute_tool(tool_name: str, tool_input: dict) -> str:
    if tool_name == "calculator":
        return calculate_expression(tool_input.get("expression", ""))
    elif tool_name == "lookup_capital":
        return lookup_capital(tool_input.get("country", ""))
    elif tool_name == "count_words":
        return count_words(tool_input.get("text", ""))
    raise ValueError(f"Unknown tool: {tool_name}")


def _format_tool_result_message(tool_name: str, tool_input: dict, tool_output: str) -> str:
    return f"I used {tool_name} with input {tool_input} and got: {tool_output}"


def _call_anthropic(
    client: Anthropic,
    config: AnthropicReactConfig,
    messages: list[dict],
    tools: list[dict],
) -> tuple[anthropic.types.Message, int]:
    started_at = time.perf_counter()
    try:
        response = client.messages.create(
            model=config.model,
            max_tokens=config.max_tokens,
            messages=messages,
            tools=tools,
        )
    except anthropic.RateLimitError as exc:
        raise ProviderError(
            f"Provider rate limit exceeded: {exc}",
            error_type="rate_limit_error",
            recovery_hint="Reduce request frequency, add retry logic with exponential backoff, or upgrade your API plan.",
        ) from exc
    except anthropic.APIStatusError as exc:
        error_type = "api_status_error"
        recovery_hint = None
        if exc.status_code == 401:
            recovery_hint = "Verify your ANTHROPIC_AUTH_TOKEN or ANTHROPIC_API_KEY is valid and not expired."
            error_type = "authentication_error"
        elif exc.status_code == 403:
            recovery_hint = "Check that your API key has access to the specified model and that model ID is correct."
            error_type = "permission_error"
        elif exc.status_code == 404:
            recovery_hint = f"Model '{config.model}' not found. Verify the model ID is correct and available in your region."
            error_type = "model_not_found"
        raise ProviderError(f"Provider API error (status={exc.status_code}): {exc}", error_type=error_type, recovery_hint=recovery_hint) from exc
    except anthropic.APIConnectionError as exc:
        raise ProviderError(f"Failed to connect to provider: {exc}", error_type="connection_error", recovery_hint="Check network connectivity and verify ANTHROPIC_BASE_URL is correct.") from exc
    except anthropic.APITimeoutError as exc:
        raise ProviderError(f"Provider request timed out after {config.timeout_seconds}s: {exc}", error_type="timeout_error", recovery_hint=f"Increase API_TIMEOUT_MS. Current timeout: {config.timeout_seconds}s.") from exc
    except anthropic.APIError as exc:
        raise ProviderError(f"Provider request failed: {exc}", error_type="api_error", recovery_hint="Verify your API credentials and model configuration.") from exc

    latency_ms = int((time.perf_counter() - started_at) * 1000)
    return response, latency_ms


def _extract_message_content(response: anthropic.types.Message) -> tuple[str | None, list[dict]]:
    text_content = None
    tool_uses = []

    for block in response.content:
        if getattr(block, "type", "") == "text":
            text_content = getattr(block, "text", "")
        elif getattr(block, "type", "") == "tool_use":
            tool_uses.append({
                "id": getattr(block, "id", ""),
                "name": getattr(block, "name", ""),
                "input": getattr(block, "input", {}),
            })

    return text_content, tool_uses


def _track_usage(
    config: AnthropicReactConfig,
    latency_ms: int,
    usage: anthropic.types.Usage,
    request_id: str | None,
) -> TokenUsage:
    usage_entry = build_usage_entry(
        model=config.model,
        base_url=config.base_url,
        max_tokens=config.max_tokens,
        latency_ms=latency_ms,
        usage=usage,
        request_id=request_id,
    )
    try:
        append_usage_entry(usage_entry)
    except OSError as exc:
        print(f"Warning: failed to write token usage log: {exc}", file=sys.stderr)

    try:
        rolling_tpm = calculate_rolling_tpm(read_usage_entries())
    except OSError as exc:
        print(f"Warning: failed to read token usage log: {exc}", file=sys.stderr)
        rolling_tpm = usage_entry.total_tokens

    average_tpm = calculate_average_tpm(usage_entry)
    print(f"input tokens: {usage_entry.input_tokens}")
    print(f"output tokens: {usage_entry.output_tokens}")
    print(f"total tokens: {usage_entry.total_tokens}")
    print(f"average output TPM: {average_tpm.output_tpm:.2f}")
    print(f"rolling TPM: {rolling_tpm}")

    return TokenUsage(
        input_tokens=usage_entry.input_tokens,
        output_tokens=usage_entry.output_tokens,
        total_tokens=usage_entry.total_tokens,
    )


def create_anthropic_react_workflow(config: AnthropicReactConfig) -> WorkflowDefinition:
    client = create_client(config)
    tools = _build_tool_definitions()

    def initial_state(user_input: str) -> WorkflowState:
        return {
            **build_default_workflow_state(user_input),
            "tool_history": [],
            "tool_calls": 0,
        }

    def graph_factory(runtime: Any) -> Any:
        def normalize_input(state: WorkflowState) -> WorkflowState:
            return {
                **state,
                "normalized_input": normalize_whitespace(state["user_input"]),
            }

        def reason_with_model(state: WorkflowState) -> WorkflowState:
            tool_history = list(state.get("tool_history") or [])
            messages = [{"role": "user", "content": state["normalized_input"]}]

            for tool_call in tool_history:
                messages.append({
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": f"tool_{tool_call['tool_calls']}",
                            "name": tool_call["tool_name"],
                            "input": {tool_call["input_key"]: tool_call["tool_input"]},
                        }
                    ],
                })
                messages.append({
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": f"tool_{tool_call['tool_calls']}",
                            "content": tool_call["tool_output"],
                        }
                    ],
                })

            response, latency_ms = _call_anthropic(client, config, messages, tools)
            text_content, tool_uses = _extract_message_content(response)

            usage = _track_usage(
                config,
                latency_ms,
                response.usage,
                getattr(response, "_request_id", None),
            )

            tool_history = list(state.get("tool_history") or [])
            for i, tool_use in enumerate(tool_uses):
                tool_history.append({
                    "tool_name": tool_use["name"],
                    "tool_input": list(tool_use["input"].values())[0] if tool_use["input"] else "",
                    "input_key": list(tool_use["input"].keys())[0] if tool_use["input"] else "",
                    "tool_output": _execute_tool(tool_use["name"], tool_use["input"]),
                    "tool_calls": i,
                })

            return {
                **state,
                "response": text_content,
                "tool_history": tool_history,
                "tool_calls": len(tool_history),
                "usage": usage,
            }

        def route_after_reason(state: WorkflowState) -> str:
            tool_history = list(state.get("tool_history") or [])
            current_tool_calls = state.get("tool_calls", 0)
            previous_tool_calls = len([t for t in tool_history if t.get("tool_calls", -1) < current_tool_calls])

            if tool_history and current_tool_calls > previous_tool_calls:
                if current_tool_calls >= config.max_tool_calls:
                    return "respond"
                return "reason_with_model"

            if state.get("response"):
                return "respond"

            return "respond"

        def respond(state: WorkflowState) -> WorkflowState:
            tool_history = list(state.get("tool_history") or [])
            if tool_history:
                response_parts = []
                for tc in tool_history:
                    response_parts.append(
                        _format_tool_result_message(tc["tool_name"], {tc["input_key"]: tc["tool_input"]}, tc["tool_output"])
                    )
                response = " | ".join(response_parts)
            else:
                response = f"Echo: {state['normalized_input']}"
            return {
                **state,
                "response": response,
            }

        graph = StateGraph(WorkflowState)
        graph.add_node(
            "normalize_input",
            runtime.tool("normalize_input", "normalize_whitespace", normalize_input),
        )
        graph.add_node("reason_with_model", runtime.node("reason_with_model", reason_with_model))
        graph.add_node("respond", runtime.node("respond", respond))
        graph.add_edge(START, "normalize_input")
        graph.add_edge("normalize_input", "reason_with_model")
        graph.add_conditional_edges(
            "reason_with_model",
            route_after_reason,
            {
                "reason_with_model": "reason_with_model",
                "respond": "respond",
            },
        )
        graph.add_edge("respond", END)
        return graph.compile()

    return WorkflowDefinition(
        name="anthropic.react",
        normalize_input=normalize_whitespace,
        initial_state=initial_state,
        graph_factory=graph_factory,
        model_name_hint=config.model,
    )


def build_anthropic_react_workflow(
    run: RunRecord | None = None,
    *,
    model_override: str | None = None,
    max_tool_calls_override: int | None = None,
) -> WorkflowDefinition:
    workflow_config = WorkflowConfig()
    if run is not None:
        workflow_config = run.workflow_config
    return create_anthropic_react_workflow(
        load_config(workflow_config, model_override=model_override, max_tool_calls_override=max_tool_calls_override)
    )
