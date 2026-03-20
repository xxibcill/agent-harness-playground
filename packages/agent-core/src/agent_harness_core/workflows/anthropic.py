from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import anthropic
from agent_harness_contracts import RunRecord, TokenUsage, WorkflowConfig
from anthropic import Anthropic
from dotenv import load_dotenv

from agent_harness_core.usage_tracker import (
    append_usage_entry,
    build_usage_entry,
    calculate_average_tpm,
    calculate_rolling_tpm,
    read_usage_entries,
)
from agent_harness_core.workflows.demo_echo import normalize_whitespace
from agent_harness_core.workflows.types import WorkflowDefinition, WorkflowResponse


class ConfigurationError(ValueError):
    """Raised when the Anthropic client configuration is incomplete."""


@dataclass(frozen=True)
class AnthropicWorkflowConfig:
    api_key: str
    model: str
    base_url: str | None
    timeout_seconds: float
    max_tokens: int


PROJECT_ROOT = Path(__file__).resolve().parents[5]
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"


def load_project_env(env_file: Path = DEFAULT_ENV_FILE) -> None:
    if env_file.exists():
        load_dotenv(env_file, override=False)


def load_config(
    workflow_config: WorkflowConfig | str | None = None,
    *,
    model_override: str | None = None,
) -> AnthropicWorkflowConfig:
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
        max_tokens_text = os.getenv("ANTHROPIC_MAX_TOKENS", "512")
        try:
            max_tokens = int(max_tokens_text)
        except ValueError as exc:
            raise ConfigurationError("ANTHROPIC_MAX_TOKENS must be an integer.") from exc

    base_url_override = None
    client_timeout_override = None
    if resolved_workflow_config.runtime_overrides is not None:
        base_url_override = resolved_workflow_config.runtime_overrides.base_url
        client_timeout_override = resolved_workflow_config.runtime_overrides.client_timeout_seconds

    return AnthropicWorkflowConfig(
        api_key=api_key,
        model=model,
        base_url=base_url_override or os.getenv("ANTHROPIC_BASE_URL"),
        timeout_seconds=float(client_timeout_override or timeout_seconds),
        max_tokens=max_tokens,
    )


def create_client(config: AnthropicWorkflowConfig) -> Anthropic:
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


def create_anthropic_workflow(config: AnthropicWorkflowConfig) -> WorkflowDefinition:
    client = create_client(config)

    def generate_response(normalized_input: str) -> WorkflowResponse:
        started_at = time.perf_counter()
        try:
            response = client.messages.create(
                model=config.model,
                max_tokens=config.max_tokens,
                messages=[{"role": "user", "content": normalized_input}],
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

        return WorkflowResponse(
            response=extract_text(response),
            model_name=config.model,
            usage=TokenUsage(
                input_tokens=usage_entry.input_tokens,
                output_tokens=usage_entry.output_tokens,
                total_tokens=usage_entry.total_tokens,
            ),
        )

    return WorkflowDefinition(
        name="anthropic.respond",
        normalize_input=normalize_whitespace,
        generate_response=generate_response,
        model_name_hint=config.model,
    )


def build_anthropic_workflow(
    run: RunRecord | None = None,
    *,
    model_override: str | None = None,
) -> WorkflowDefinition:
    workflow_config = WorkflowConfig()
    if run is not None:
        workflow_config = run.workflow_config
    return create_anthropic_workflow(
        load_config(workflow_config, model_override=model_override)
    )
