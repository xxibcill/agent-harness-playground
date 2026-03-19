from basic_langgraph_agent.agent import build_graph


def test_basic_agent_returns_a_response() -> None:
    graph = build_graph()

    result = graph.invoke({"user_input": "Test run", "response": ""})

    assert result["response"] == "Hello from LangGraph. You said: Test run"
