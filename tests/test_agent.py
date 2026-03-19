import os
from types import SimpleNamespace

from basic_langgraph_agent import agent
from basic_langgraph_agent.agent import (
    AgentConfig,
    build_graph,
    create_responder,
    load_config,
    load_project_env,
)


def test_basic_agent_returns_a_response() -> None:
    graph = build_graph(lambda user_input: f"Echo: {user_input}")

    result = graph.invoke({"user_input": "Test run", "response": ""})

    assert result["response"] == "Echo: Test run"


def test_load_config_reads_custom_anthropic_env(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "test-token")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://api.z.ai/api/anthropic")
    monkeypatch.setenv("API_TIMEOUT_MS", "3000000")
    monkeypatch.setenv("ANTHROPIC_MAX_TOKENS", "256")

    config = load_config("provider-model")

    assert config == AgentConfig(
        api_key="test-token",
        model="provider-model",
        base_url="https://api.z.ai/api/anthropic",
        timeout_seconds=3000.0,
        max_tokens=256,
    )


def test_create_responder_returns_text_from_anthropic_message(monkeypatch) -> None:
    class FakeMessages:
        def create(self, **_: object) -> SimpleNamespace:
            return SimpleNamespace(
                content=[
                    SimpleNamespace(type="text", text="First line"),
                    SimpleNamespace(type="text", text="Second line"),
                ]
            )

    class FakeClient:
        messages = FakeMessages()

    monkeypatch.setattr(agent, "create_client", lambda _: FakeClient())

    responder = create_responder(
        AgentConfig(
            api_key="test-token",
            model="provider-model",
            base_url="https://api.z.ai/api/anthropic",
            timeout_seconds=3000.0,
            max_tokens=256,
        )
    )

    assert responder("hello") == "First line\nSecond line"


def test_load_project_env_reads_dotenv_without_overriding_existing_env(tmp_path, monkeypatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        'ANTHROPIC_AUTH_TOKEN="file-token"\nANTHROPIC_MODEL="file-model"\n',
        encoding="utf-8",
    )

    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    load_project_env(env_file)

    assert os.getenv("ANTHROPIC_AUTH_TOKEN") == "file-token"
    assert os.getenv("ANTHROPIC_MODEL") == "file-model"

    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "shell-token")
    load_project_env(env_file)

    assert os.getenv("ANTHROPIC_AUTH_TOKEN") == "shell-token"
