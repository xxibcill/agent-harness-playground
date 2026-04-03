from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

from agent_harness_core.workflows.demo_echo import normalize_whitespace
from agent_harness_core.workflows.types import (
    WorkflowDefinition,
    WorkflowState,
    build_default_workflow_state,
)


class RouteWorkflowState(TypedDict, total=False):
    user_input: str
    normalized_input: str
    response: str
    model_output: dict[str, Any] | None
    category: str | None


_CATEGORY_GREETING = "greeting"
_CATEGORY_QUESTION = "question"
_CATEGORY_COMMAND = "command"
_CATEGORY_STATEMENT = "statement"

_CATEGORIES = (_CATEGORY_GREETING, _CATEGORY_QUESTION, _CATEGORY_COMMAND, _CATEGORY_STATEMENT)

_GREETING_KEYWORDS = (
    "hello", "hi", "hey", "greetings", "good morning", "good afternoon", "good evening"
)
_QUESTION_MARKERS = ("?", "what", "how", "why", "when", "where", "who", "which", "can you", "could you")
_COMMAND_KEYWORDS = ("run", "execute", "start", "stop", "do", "make", "create", "delete", "please")


def classify_input(normalized_input: str) -> str:
    lowered = normalized_input.lower().strip()
    
    if any(lowered.startswith(kw) for kw in _GREETING_KEYWORDS):
        return _CATEGORY_GREETING
    
    if any(lowered.startswith(kw) for kw in _COMMAND_KEYWORDS):
        return _CATEGORY_COMMAND
    
    if "?" in lowered or any(lowered.startswith(kw) for kw in _QUESTION_MARKERS):
        return _CATEGORY_QUESTION
    
    return _CATEGORY_STATEMENT


def create_demo_route_workflow() -> WorkflowDefinition:
    def initial_state(user_input: str) -> RouteWorkflowState:
        return {
            **build_default_workflow_state(user_input),
            "category": None,
        }

    def graph_factory(runtime: Any) -> Any:
        def normalize_input(state: RouteWorkflowState) -> RouteWorkflowState:
            return {
                **state,
                "normalized_input": normalize_whitespace(state["user_input"]),
            }

        def classify(state: RouteWorkflowState) -> RouteWorkflowState:
            category = classify_input(state["normalized_input"])
            return {
                **state,
                "category": category,
            }

        def respond_greeting(state: RouteWorkflowState) -> RouteWorkflowState:
            return {
                **state,
                "response": "Hello! I'm here to help. What would you like to do?",
            }

        def respond_question(state: RouteWorkflowState) -> RouteWorkflowState:
            normalized = state["normalized_input"]
            return {
                **state,
                "response": f"That's an interesting question. You asked: '{normalized}'. Let me think about it.",
            }

        def respond_command(state: RouteWorkflowState) -> RouteWorkflowState:
            normalized = state["normalized_input"]
            return {
                **state,
                "response": f"I'll execute that command: '{normalized}'. Done!",
            }

        def respond_statement(state: RouteWorkflowState) -> RouteWorkflowState:
            normalized = state["normalized_input"]
            return {
                **state,
                "response": f"I see. You said: '{normalized}'. Thanks for sharing.",
            }

        def route_by_category(state: RouteWorkflowState) -> str:
            category = state.get("category", _CATEGORY_STATEMENT)
            return f"respond_{category}"

        graph = StateGraph(RouteWorkflowState)
        graph.add_node(
            "normalize_input",
            runtime.tool("normalize_input", "normalize_whitespace", normalize_input),
        )
        graph.add_node("classify", runtime.node("classify", classify))
        graph.add_node("respond_greeting", runtime.node("respond_greeting", respond_greeting))
        graph.add_node("respond_question", runtime.node("respond_question", respond_question))
        graph.add_node("respond_command", runtime.node("respond_command", respond_command))
        graph.add_node("respond_statement", runtime.node("respond_statement", respond_statement))

        graph.add_edge(START, "normalize_input")
        graph.add_edge("normalize_input", "classify")
        graph.add_conditional_edges(
            "classify",
            route_by_category,
            {
                "respond_greeting": "respond_greeting",
                "respond_question": "respond_question",
                "respond_command": "respond_command",
                "respond_statement": "respond_statement",
            },
        )
        graph.add_edge("respond_greeting", END)
        graph.add_edge("respond_question", END)
        graph.add_edge("respond_command", END)
        graph.add_edge("respond_statement", END)

        return graph.compile()

    return WorkflowDefinition(
        name="demo.route",
        normalize_input=normalize_whitespace,
        initial_state=initial_state,
        graph_factory=graph_factory,
    )