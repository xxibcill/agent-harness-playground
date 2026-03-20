from __future__ import annotations

from typing import Any, Callable

from langgraph.graph import END, START, StateGraph

from agent_harness_core.workflows.types import WorkflowDefinition, WorkflowState


def compile_workflow_graph(
    normalize_input: Callable[[WorkflowState], WorkflowState],
    generate_response: Callable[[WorkflowState], WorkflowState],
) -> Any:
    graph = StateGraph(WorkflowState)
    graph.add_node("normalize_input", normalize_input)
    graph.add_node("generate_response", generate_response)
    graph.add_edge(START, "normalize_input")
    graph.add_edge("normalize_input", "generate_response")
    graph.add_edge("generate_response", END)
    return graph.compile()


def build_workflow_graph(workflow: WorkflowDefinition) -> Any:
    def normalize_input(state: WorkflowState) -> WorkflowState:
        normalized_input = workflow.normalize_input(state["user_input"])
        return {
            **state,
            "normalized_input": normalized_input,
        }

    def generate_response(state: WorkflowState) -> WorkflowState:
        result = workflow.generate_response(state["normalized_input"])
        return {
            **state,
            "response": result.response,
        }

    return compile_workflow_graph(normalize_input, generate_response)
