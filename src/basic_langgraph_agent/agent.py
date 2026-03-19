from __future__ import annotations

import argparse
from typing import Any

from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict


class AgentState(TypedDict):
    user_input: str
    response: str


def build_graph() -> Any:
    graph = StateGraph(AgentState)
    graph.add_node("respond", respond)
    graph.add_edge(START, "respond")
    graph.add_edge("respond", END)
    return graph.compile()


def respond(state: AgentState) -> AgentState:
    user_input = state["user_input"].strip()
    response = f"Hello from LangGraph. You said: {user_input}"
    return {
        "user_input": user_input,
        "response": response,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the basic LangGraph agent.")
    parser.add_argument(
        "message",
        nargs="?",
        default="Say hello to LangGraph",
        help="Message to pass into the graph.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    graph = build_graph()
    result = graph.invoke({"user_input": args.message, "response": ""})
    print(result["response"])


if __name__ == "__main__":
    main()
