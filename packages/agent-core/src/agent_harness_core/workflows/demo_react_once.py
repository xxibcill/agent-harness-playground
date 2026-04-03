from __future__ import annotations

from typing import Any, cast

from langgraph.graph import END, START, StateGraph

from agent_harness_core.workflows.demo_echo import normalize_whitespace
from agent_harness_core.workflows.react import (
    _format_tool_response,
    calculate_expression,
    count_words,
    lookup_capital,
)
from agent_harness_core.workflows.types import (
    WorkflowDefinition,
    WorkflowState,
    build_default_workflow_state,
)

ToolFn = Any


def create_demo_react_once_workflow(tools: dict[str, ToolFn] | None = None) -> WorkflowDefinition:
    available_tools = tools or {
        "calculator": calculate_expression,
        "lookup_capital": lookup_capital,
        "count_words": count_words,
    }

    def initial_state(user_input: str) -> WorkflowState:
        return {
            **build_default_workflow_state(user_input),
            "selected_tool": None,
            "tool_input": None,
            "tool_output": None,
            "thought": "",
            "tool_history": [],
            "tool_calls": 0,
        }

    def graph_factory(runtime: Any) -> Any:
        from agent_harness_core.workflows.react import choose_tool

        def normalize_input(state: WorkflowState) -> WorkflowState:
            return {
                **state,
                "normalized_input": normalize_whitespace(state["user_input"]),
            }

        def reason(state: WorkflowState) -> WorkflowState:
            selected_tool, tool_input = choose_tool(state["normalized_input"])
            if selected_tool is None:
                thought = "I can answer directly without using a tool."
            else:
                thought = f"I should use {selected_tool} to inspect {tool_input}."
            return {
                **state,
                "selected_tool": selected_tool,
                "tool_input": tool_input,
                "thought": thought,
            }

        def use_tool(state: WorkflowState) -> WorkflowState:
            tool_name = state["selected_tool"]
            if tool_name is None:
                return state
            tool = available_tools.get(tool_name)
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
                response = _format_tool_response(
                    last_step["tool_name"],
                    last_step["tool_input"],
                    last_step["tool_output"],
                )
            else:
                response = f"I did not need a tool. Echo: {state['normalized_input']}"
            return {
                **state,
                "response": response,
            }

        def route_after_reason(state: WorkflowState) -> str:
            if state.get("selected_tool") is None:
                return "respond"
            return "use_tool"

        graph = StateGraph(WorkflowState)
        graph.add_node(
            "normalize_input",
            runtime.tool("normalize_input", "normalize_whitespace", normalize_input),
        )
        graph.add_node("reason", runtime.node("reason", reason))
        graph.add_node(
            "use_tool",
            runtime.tool(
                "use_tool",
                lambda state: cast(str, state.get("selected_tool") or "react_tool"),
                use_tool,
            ),
        )
        graph.add_node("respond", runtime.node("respond", respond))
        graph.add_edge(START, "normalize_input")
        graph.add_edge("normalize_input", "reason")
        graph.add_conditional_edges(
            "reason",
            route_after_reason,
            {
                "use_tool": "use_tool",
                "respond": "respond",
            },
        )
        graph.add_edge("use_tool", "respond")
        graph.add_edge("respond", END)
        return graph.compile()

    return WorkflowDefinition(
        name="demo.react.once",
        normalize_input=normalize_whitespace,
        initial_state=initial_state,
        graph_factory=graph_factory,
    )
