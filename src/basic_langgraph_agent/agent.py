from __future__ import annotations

import argparse

from agent_harness_core.workflows import (
    AnthropicWorkflowConfig as AgentConfig,
)
from agent_harness_core.workflows import (
    ConfigurationError,
    create_anthropic_workflow,
    load_config,
)
from agent_harness_core.workflows.demo_echo import normalize_whitespace
from agent_harness_core.workflows.graph import build_workflow_graph
from agent_harness_core.workflows.types import WorkflowDefinition, WorkflowResponse


def build_graph(responder):
    workflow = WorkflowDefinition(
        name="basic.cli",
        normalize_input=normalize_whitespace,
        generate_response=lambda normalized_input: WorkflowResponse(
            response=responder(normalized_input),
            model_name="basic-cli-model",
        ),
        model_name_hint="basic-cli-model",
    )
    return build_workflow_graph(workflow)


def create_responder(config: AgentConfig):
    workflow = create_anthropic_workflow(config)
    return lambda user_input: workflow.generate_response(normalize_whitespace(user_input)).response


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
        result = graph.invoke({"user_input": args.message, "normalized_input": "", "response": ""})
    except (ConfigurationError, RuntimeError) as exc:
        raise SystemExit(str(exc)) from exc

    print(result["response"])


if __name__ == "__main__":
    main()
