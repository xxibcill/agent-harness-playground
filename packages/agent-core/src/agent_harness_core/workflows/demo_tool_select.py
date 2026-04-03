from __future__ import annotations

from typing import Any, cast

from langgraph.graph import END, StateGraph

from agent_harness_core.workflows.demo_echo import normalize_whitespace
from agent_harness_core.workflows.react import (
    calculate_expression,
    choose_tool,
    count_words,
    lookup_capital,
)
from agent_harness_core.workflows.types import (
    WorkflowDefinition,
    WorkflowState,
    build_default_workflow_state,
)

_TOOLS = {
    "calculator": calculate_expression,
    "lookup_capital": lookup_capital,
    "count_words": count_words,
}


def create_demo_tool_select_workflow() -> WorkflowDefinition:
    def initial_state(user_input: str) -> WorkflowState:
        return {
            **build_default_workflow_state(user_input),
            "selected_tool": None,
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

        def select_tool(state: WorkflowState) -> WorkflowState:
            selected_tool, tool_input = choose_tool(state["normalized_input"])
            return {
                **state,
                "selected_tool": selected_tool,
                "tool_input": tool_input,
            }

        def execute_tool(state: WorkflowState) -> WorkflowState:
            tool_name = state.get("selected_tool")
            if tool_name is None:
                return state
            tool = _TOOLS.get(tool_name)
            if tool is None:
                raise ValueError(f"Unknown tool: {tool_name}")
            tool_input = state.get("tool_input") or ""
            tool_output = tool(tool_input)
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
                response = (
                    f"Selected tool: {last_step['tool_name']}. "
                    f"Result: {last_step['tool_output']} "
                    f"(input was: '{last_step['tool_input']}')"
                )
            else:
                response = (
                    f"No matching tool found for: '{state['normalized_input']}'. "
                    f"Supported patterns: math expressions, 'capital of <country>', "
                    f"'count words in <text>'."
                )
            return {
                **state,
                "response": response,
            }

        def route_after_select(state: WorkflowState) -> str:
            if state.get("selected_tool") is None:
                return "respond"
            return "execute_tool"

        graph = StateGraph(WorkflowState)
        graph.add_node(
            "normalize_input",
            runtime.tool("normalize_input", "normalize_whitespace", normalize_input),
        )
        graph.add_node("select_tool", runtime.node("select_tool", select_tool))
        graph.add_node(
            "execute_tool",
            runtime.tool(
                "execute_tool",
                lambda state: cast(str, state.get("selected_tool") or "none"),
                execute_tool,
            ),
        )
        graph.add_node("respond", runtime.node("respond", respond))

        graph.add_edge("__start__", "normalize_input")
        graph.add_edge("normalize_input", "select_tool")
        graph.add_conditional_edges(
            "select_tool",
            route_after_select,
            {
                "execute_tool": "execute_tool",
                "respond": "respond",
            },
        )
        graph.add_edge("execute_tool", "respond")
        graph.add_edge("respond", END)

        return graph.compile()

    return WorkflowDefinition(
        name="demo.tool.select",
        normalize_input=normalize_whitespace,
        initial_state=initial_state,
        graph_factory=graph_factory,
    )
