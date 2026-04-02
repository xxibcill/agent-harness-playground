from __future__ import annotations

import argparse
import ast
import operator
import re
from collections.abc import Callable
from typing import Any

from agent_harness_core.workflows.demo_echo import normalize_whitespace
from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

ToolFn = Callable[[str], str]

_CAPITALS = {
    "france": "Paris",
    "japan": "Tokyo",
    "thailand": "Bangkok",
    "united states": "Washington, D.C.",
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


class ToolAgentState(TypedDict):
    user_input: str
    normalized_input: str
    selected_tool: str | None
    tool_input: str | None
    tool_output: str | None
    response: str


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


def choose_tool(normalized_input: str) -> tuple[str | None, str | None]:
    calculation = _extract_math_expression(normalized_input)
    if calculation is not None:
        return "calculator", calculation

    capital_lookup = _extract_capital_lookup_target(normalized_input)
    if capital_lookup is not None:
        return "lookup_capital", capital_lookup

    word_count = _extract_word_count_target(normalized_input)
    if word_count is not None:
        return "count_words", word_count

    return None, None


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


def build_tool_agent_graph(tools: dict[str, ToolFn] | None = None) -> Any:
    available_tools = tools or {
        "calculator": calculate_expression,
        "lookup_capital": lookup_capital,
        "count_words": count_words,
    }

    def normalize_input(state: ToolAgentState) -> ToolAgentState:
        return {
            **state,
            "normalized_input": normalize_whitespace(state["user_input"]),
        }

    def plan(state: ToolAgentState) -> ToolAgentState:
        selected_tool, tool_input = choose_tool(state["normalized_input"])
        return {
            **state,
            "selected_tool": selected_tool,
            "tool_input": tool_input,
        }

    def use_tool(state: ToolAgentState) -> ToolAgentState:
        tool_name = state["selected_tool"]
        if tool_name is None:
            return state
        tool = available_tools.get(tool_name)
        if tool is None:
            raise ValueError(f"Unknown tool: {tool_name}")
        return {
            **state,
            "tool_output": tool(state["tool_input"] or ""),
        }

    def respond(state: ToolAgentState) -> ToolAgentState:
        tool_name = state["selected_tool"]
        if tool_name is None:
            response = f"I did not need a tool. Echo: {state['normalized_input']}"
        else:
            response = _format_tool_response(
                tool_name,
                state["tool_input"] or "",
                state["tool_output"] or "",
            )
        return {
            **state,
            "response": response,
        }

    def route_after_plan(state: ToolAgentState) -> str:
        if state["selected_tool"] is None:
            return "respond"
        return "use_tool"

    graph = StateGraph(ToolAgentState)
    graph.add_node("normalize_input", normalize_input)
    graph.add_node("plan", plan)
    graph.add_node("use_tool", use_tool)
    graph.add_node("respond", respond)
    graph.add_edge(START, "normalize_input")
    graph.add_edge("normalize_input", "plan")
    graph.add_conditional_edges(
        "plan",
        route_after_plan,
        {
            "use_tool": "use_tool",
            "respond": "respond",
        },
    )
    graph.add_edge("use_tool", "respond")
    graph.add_edge("respond", END)
    return graph.compile()


def _format_tool_response(tool_name: str, tool_input: str, tool_output: str) -> str:
    if tool_name == "calculator":
        return f"I used calculator on {tool_input} and got {tool_output}."
    if tool_name == "lookup_capital":
        return f"I used lookup_capital and found: {tool_input} -> {tool_output}."
    if tool_name == "count_words":
        return f"I used count_words and found {tool_output} words in: {tool_input}"
    return f"I used {tool_name} and got {tool_output}."


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the basic LangGraph tool agent.")
    parser.add_argument(
        "message",
        nargs="?",
        default="Calculate 2 + 2",
        help="Message to pass into the tool-capable graph.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    graph = build_tool_agent_graph()
    result = graph.invoke(
        {
            "user_input": args.message,
            "normalized_input": "",
            "selected_tool": None,
            "tool_input": None,
            "tool_output": None,
            "response": "",
        }
    )
    print(result["response"])


if __name__ == "__main__":
    main()
