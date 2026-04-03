from __future__ import annotations

from typing import Any, cast

from langgraph.graph import END, StateGraph

from agent_harness_core.workflows.demo_echo import normalize_whitespace
from agent_harness_core.workflows.react import count_words
from agent_harness_core.workflows.types import (
    WorkflowDefinition,
    WorkflowState,
    build_default_workflow_state,
)


def create_demo_tool_single_workflow() -> WorkflowDefinition:
    def initial_state(user_input: str) -> WorkflowState:
        return {
            **build_default_workflow_state(user_input),
            "selected_tool": "count_words",
            "tool_input": None,
            "tool_output": None,
            "tool_history": [],
            "tool_calls": 0,
        }

    def graph_factory(runtime: Any) -> Any:
        def normalize_input(state: WorkflowState) -> WorkflowState:
            return {
                **state,
                "normalized_input": normalize_whitespace(state["user_input"]),
            }

        def prepare_tool_call(state: WorkflowState) -> WorkflowState:
            tool_input = state["normalized_input"]
            return {
                **state,
                "selected_tool": "count_words",
                "tool_input": tool_input,
            }

        def execute_tool(state: WorkflowState) -> WorkflowState:
            tool_name = "count_words"
            tool_input = state.get("tool_input") or ""
            tool_output = count_words(tool_input)
            tool_history = list(state.get("tool_history") or [])
            tool_history.append(
                {
                    "tool_name": tool_name,
                    "tool_input": tool_input,
                    "tool_output": tool_output,
                }
            )
            return {
                **state,
                "tool_output": tool_output,
                "tool_history": tool_history,
                "tool_calls": int(state.get("tool_calls") or 0) + 1,
            }

        def respond(state: WorkflowState) -> WorkflowState:
            tool_history = list(state.get("tool_history") or [])
            if tool_history:
                last_step = tool_history[-1]
                response = f"Word count result: '{last_step['tool_input']}' has {last_step['tool_output']} words. (Tool: {last_step['tool_name']})"
            else:
                response = "No tool was called."
            return {
                **state,
                "response": response,
            }

        graph = StateGraph(WorkflowState)
        graph.add_node(
            "normalize_input",
            runtime.tool("normalize_input", "normalize_whitespace", normalize_input),
        )
        graph.add_node(
            "prepare_tool_call",
            runtime.node("prepare_tool_call", prepare_tool_call),
        )
        graph.add_node(
            "execute_tool",
            runtime.tool(
                "execute_tool",
                lambda state: cast(str, state.get("selected_tool") or "count_words"),
                execute_tool,
            ),
        )
        graph.add_node("respond", runtime.node("respond", respond))

        graph.add_edge("__start__", "normalize_input")
        graph.add_edge("normalize_input", "prepare_tool_call")
        graph.add_edge("prepare_tool_call", "execute_tool")
        graph.add_edge("execute_tool", "respond")
        graph.add_edge("respond", END)

        return graph.compile()

    return WorkflowDefinition(
        name="demo.tool.single",
        normalize_input=normalize_whitespace,
        initial_state=initial_state,
        graph_factory=graph_factory,
    )
