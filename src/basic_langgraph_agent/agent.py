from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import anthropic
from anthropic import Anthropic
from langgraph.graph import END, START, StateGraph
from dotenv import load_dotenv
from typing_extensions import TypedDict

from basic_langgraph_agent.usage_tracker import (
    append_usage_entry,
    build_usage_entry,
    calculate_average_tpm,
    calculate_rolling_tpm,
    read_usage_entries,
)


class AgentState(TypedDict):
    user_input: str
    response: str


class ConfigurationError(ValueError):
    """Raised when the Anthropic client configuration is incomplete."""


@dataclass(frozen=True)
class AgentConfig:
    api_key: str
    model: str
    base_url: str | None
    timeout_seconds: float
    max_tokens: int


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"


def build_graph(responder: Callable[[str], str]) -> Any:
    graph = StateGraph(AgentState)
    graph.add_node("respond", create_respond_node(responder))
    graph.add_edge(START, "respond")
    graph.add_edge("respond", END)
    return graph.compile()


def create_respond_node(responder: Callable[[str], str]) -> Callable[[AgentState], AgentState]:
    def respond(state: AgentState) -> AgentState:
        user_input = state["user_input"].strip()
        response = responder(user_input)
        return {
            "user_input": user_input,
            "response": response,
        }

    return respond


def load_project_env(env_file: Path = DEFAULT_ENV_FILE) -> None:
    if env_file.exists():
        load_dotenv(env_file, override=False)


def load_config(model_override: str | None) -> AgentConfig:
    load_project_env()

    api_key = os.getenv("ANTHROPIC_AUTH_TOKEN") or os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ConfigurationError(
            "Set ANTHROPIC_AUTH_TOKEN or ANTHROPIC_API_KEY before running the agent."
        )

    model = model_override or os.getenv("ANTHROPIC_MODEL")
    if not model:
        raise ConfigurationError(
            "Pass --model or set ANTHROPIC_MODEL to a model ID supported by your Anthropic-compatible endpoint."
        )

    timeout_ms = os.getenv("API_TIMEOUT_MS", "600000")
    try:
        timeout_seconds = int(timeout_ms) / 1000
    except ValueError as exc:
        raise ConfigurationError("API_TIMEOUT_MS must be an integer number of milliseconds.") from exc

    max_tokens_text = os.getenv("ANTHROPIC_MAX_TOKENS", "512")
    try:
        max_tokens = int(max_tokens_text)
    except ValueError as exc:
        raise ConfigurationError("ANTHROPIC_MAX_TOKENS must be an integer.") from exc

    return AgentConfig(
        api_key=api_key,
        model=model,
        base_url=os.getenv("ANTHROPIC_BASE_URL"),
        timeout_seconds=timeout_seconds,
        max_tokens=max_tokens,
    )


def create_client(config: AgentConfig) -> Anthropic:
    return Anthropic(
        api_key=config.api_key,
        base_url=config.base_url,
        timeout=config.timeout_seconds,
    )


def extract_text(response: anthropic.types.Message) -> str:
    parts = [
        block.text.strip()
        for block in response.content
        if getattr(block, "type", "") == "text" and getattr(block, "text", "").strip()
    ]
    if not parts:
        raise RuntimeError("The Anthropic response did not include any text content.")
    return "\n".join(parts)


def create_responder(config: AgentConfig) -> Callable[[str], str]:
    client = create_client(config)

    def responder(user_input: str) -> str:
        started_at = time.perf_counter()
        try:
            response = client.messages.create(
                model=config.model,
                max_tokens=config.max_tokens,
                messages=[{"role": "user", "content": user_input}],
            )
        except anthropic.APIError as exc:
            raise RuntimeError(f"Anthropic request failed: {exc}") from exc

        latency_ms = int((time.perf_counter() - started_at) * 1000)
        usage_entry = build_usage_entry(
            model=config.model,
            base_url=config.base_url,
            max_tokens=config.max_tokens,
            latency_ms=latency_ms,
            usage=response.usage,
            request_id=getattr(response, "_request_id", None),
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
        print(f"average output TPM for this call: {average_tpm.output_tpm:.2f}")
        print(f"rolling TPM over the last 60 seconds: {rolling_tpm}")

        return extract_text(response)

    return responder


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the basic LangGraph agent.")
    parser.add_argument(
        "message",
        nargs="?",
        default="Say hello to LangGraph",
        help="Message to pass into the graph.",
    )
    parser.add_argument(
        "--model",
        help="Model ID accepted by your Anthropic-compatible endpoint. Overrides ANTHROPIC_MODEL.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        config = load_config(args.model)
        graph = build_graph(create_responder(config))
        result = graph.invoke({"user_input": args.message, "response": ""})
    except (ConfigurationError, RuntimeError) as exc:
        raise SystemExit(str(exc)) from exc

    print(result["response"])


if __name__ == "__main__":
    main()
