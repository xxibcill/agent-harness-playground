from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from agent_harness_core.usage_tracker import (
    AverageTpm,
    UsageEntry,
    append_usage_entry,
    build_usage_payload,
    calculate_average_tpm,
    calculate_rolling_tpm,
    format_usage_report,
    read_usage_entries,
)
from agent_harness_core.workflows import (
    AnthropicWorkflowConfig,
    ConfigurationError,
    WorkflowDefinition,
    WorkflowResponse,
    build_workflow_graph,
    create_anthropic_workflow,
    load_config,
)


def test_build_workflow_graph_returns_a_response() -> None:
    workflow = WorkflowDefinition(
        name="test.workflow",
        normalize_input=lambda user_input: user_input.strip(),
        generate_response=lambda normalized_input: WorkflowResponse(
            response=f"Echo: {normalized_input}",
            model_name="test-model",
        ),
        model_name_hint="test-model",
    )
    graph = build_workflow_graph(workflow)

    result = graph.invoke({"user_input": "Test run", "normalized_input": "", "response": ""})

    assert result["normalized_input"] == "Test run"
    assert result["response"] == "Echo: Test run"


def test_load_config_reads_custom_anthropic_env(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "test-token")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://api.z.ai/api/anthropic")
    monkeypatch.setenv("API_TIMEOUT_MS", "3000000")
    monkeypatch.setenv("ANTHROPIC_MAX_TOKENS", "256")

    config = load_config("provider-model")

    assert config == AnthropicWorkflowConfig(
        api_key="test-token",
        model="provider-model",
        base_url="https://api.z.ai/api/anthropic",
        timeout_seconds=3000.0,
        max_tokens=256,
    )


def test_load_config_fails_fast_when_api_key_is_missing(monkeypatch) -> None:
    monkeypatch.setattr("agent_harness_core.workflows.anthropic.load_project_env", lambda: None)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)

    with pytest.raises(ConfigurationError, match="ANTHROPIC_AUTH_TOKEN or ANTHROPIC_API_KEY"):
        load_config()


def test_create_anthropic_workflow_returns_text_from_anthropic_message(
    monkeypatch, capsys
) -> None:
    captured_entry = {}

    class FakeMessages:
        def create(self, **_: object) -> SimpleNamespace:
            return SimpleNamespace(
                content=[
                    SimpleNamespace(type="text", text="First line"),
                    SimpleNamespace(type="text", text="Second line"),
                ],
                usage=SimpleNamespace(
                    input_tokens=11,
                    output_tokens=7,
                    cache_creation_input_tokens=0,
                    cache_read_input_tokens=0,
                ),
                _request_id="req_test_123",
            )

    class FakeClient:
        messages = FakeMessages()

    monkeypatch.setattr(
        "agent_harness_core.workflows.anthropic.create_client",
        lambda _: FakeClient(),
    )
    monkeypatch.setattr(
        "agent_harness_core.workflows.anthropic.append_usage_entry",
        lambda entry: captured_entry.setdefault("entry", entry),
    )
    monkeypatch.setattr(
        "agent_harness_core.workflows.anthropic.read_usage_entries",
        lambda: [captured_entry["entry"]],
    )

    workflow = create_anthropic_workflow(
        AnthropicWorkflowConfig(
            api_key="test-token",
            model="provider-model",
            base_url="https://api.z.ai/api/anthropic",
            timeout_seconds=3000.0,
            max_tokens=256,
        )
    )

    response = workflow.generate_response("hello")

    assert response.response == "First line\nSecond line"
    assert captured_entry["entry"].total_tokens == 18
    output = capsys.readouterr().out
    assert "input tokens: 11" in output
    assert "output tokens: 7" in output
    assert "total tokens: 18" in output
    assert "average output TPM for this call: " in output
    assert "rolling TPM over the last 60 seconds: 18" in output


def test_create_anthropic_workflow_normalizes_before_request(monkeypatch) -> None:
    captured_messages: list[dict[str, object]] = []

    class FakeMessages:
        def create(self, **kwargs: object) -> SimpleNamespace:
            captured_messages.append(kwargs)
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text="Normalized response")],
                usage=SimpleNamespace(
                    input_tokens=3,
                    output_tokens=2,
                    cache_creation_input_tokens=0,
                    cache_read_input_tokens=0,
                ),
                _request_id="req_test_456",
            )

    class FakeClient:
        messages = FakeMessages()

    monkeypatch.setattr(
        "agent_harness_core.workflows.anthropic.create_client",
        lambda _: FakeClient(),
    )
    monkeypatch.setattr(
        "agent_harness_core.workflows.anthropic.append_usage_entry",
        lambda _entry: None,
    )
    monkeypatch.setattr(
        "agent_harness_core.workflows.anthropic.read_usage_entries",
        lambda: [],
    )

    workflow = create_anthropic_workflow(
        AnthropicWorkflowConfig(
            api_key="test-token",
            model="provider-model",
            base_url=None,
            timeout_seconds=30.0,
            max_tokens=128,
        )
    )
    graph = build_workflow_graph(workflow)

    result = graph.invoke(
        {"user_input": "  hello   workflow  ", "normalized_input": "", "response": ""}
    )

    assert result["normalized_input"] == "hello workflow"
    assert captured_messages == [
        {
            "model": "provider-model",
            "max_tokens": 128,
            "messages": [{"role": "user", "content": "hello workflow"}],
        }
    ]


def test_append_usage_entry_persists_jsonl_records(tmp_path) -> None:
    log_file = tmp_path / "token_usage.jsonl"
    entry = UsageEntry(
        timestamp_utc="2026-03-19T10:00:00+00:00",
        model="provider-model",
        base_url="https://api.z.ai/api/anthropic",
        request_id="req_test_123",
        input_tokens=12,
        output_tokens=8,
        total_tokens=20,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
        max_tokens=256,
        latency_ms=345,
    )

    append_usage_entry(entry, log_file)
    loaded_entries = read_usage_entries(log_file)

    assert loaded_entries == [entry]


def test_format_usage_report_summarizes_entries() -> None:
    report = format_usage_report(
        [
            UsageEntry(
                timestamp_utc="2026-03-19T10:00:00+00:00",
                model="model-a",
                base_url="https://api.z.ai/api/anthropic",
                request_id="req_1",
                input_tokens=10,
                output_tokens=5,
                total_tokens=15,
                cache_creation_input_tokens=2,
                cache_read_input_tokens=3,
                max_tokens=256,
                latency_ms=100,
            ),
            UsageEntry(
                timestamp_utc="2026-03-19T10:01:00+00:00",
                model="model-a",
                base_url="https://api.z.ai/api/anthropic",
                request_id="req_2",
                input_tokens=4,
                output_tokens=6,
                total_tokens=10,
                cache_creation_input_tokens=0,
                cache_read_input_tokens=1,
                max_tokens=256,
                latency_ms=120,
            ),
        ]
    )

    assert "requests=2" in report
    assert "input_tokens=14" in report
    assert "output_tokens=11" in report
    assert "total_tokens=25" in report
    assert "cache_creation_input_tokens=2" in report
    assert "cache_read_input_tokens=4" in report


def test_build_usage_payload_returns_summary_dict() -> None:
    payload = build_usage_payload(
        [
            UsageEntry(
                timestamp_utc="2026-03-19T10:00:00+00:00",
                model="model-a",
                base_url="https://api.z.ai/api/anthropic",
                request_id="req_1",
                input_tokens=10,
                output_tokens=5,
                total_tokens=15,
                cache_creation_input_tokens=2,
                cache_read_input_tokens=3,
                max_tokens=256,
                latency_ms=100,
            )
        ]
    )

    assert payload["requests"] == 1
    assert payload["input_tokens"] == 10
    assert payload["output_tokens"] == 5
    assert payload["total_tokens"] == 15
    assert payload["last_model"] == "model-a"


def test_calculate_rolling_tpm_uses_last_60_seconds_only() -> None:
    now = datetime(2026, 3, 19, 10, 1, 0, tzinfo=timezone.utc)
    rolling_tpm = calculate_rolling_tpm(
        [
            UsageEntry(
                timestamp_utc="2026-03-19T09:59:59+00:00",
                model="model-a",
                base_url=None,
                request_id="req_old",
                input_tokens=40,
                output_tokens=10,
                total_tokens=50,
                cache_creation_input_tokens=0,
                cache_read_input_tokens=0,
                max_tokens=256,
                latency_ms=80,
            ),
            UsageEntry(
                timestamp_utc="2026-03-19T10:00:20+00:00",
                model="model-a",
                base_url=None,
                request_id="req_1",
                input_tokens=8,
                output_tokens=4,
                total_tokens=12,
                cache_creation_input_tokens=0,
                cache_read_input_tokens=0,
                max_tokens=256,
                latency_ms=80,
            ),
            UsageEntry(
                timestamp_utc="2026-03-19T10:00:50+00:00",
                model="model-a",
                base_url=None,
                request_id="req_2",
                input_tokens=5,
                output_tokens=3,
                total_tokens=8,
                cache_creation_input_tokens=0,
                cache_read_input_tokens=0,
                max_tokens=256,
                latency_ms=80,
            ),
        ],
        now=now,
    )

    assert rolling_tpm == 20


def test_calculate_average_tpm_uses_request_duration() -> None:
    average_tpm = calculate_average_tpm(
        UsageEntry(
            timestamp_utc="2026-03-19T10:00:00+00:00",
            model="model-a",
            base_url=None,
            request_id="req_1",
            input_tokens=120,
            output_tokens=180,
            total_tokens=300,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
            max_tokens=512,
            latency_ms=90000,
        )
    )

    assert average_tpm == AverageTpm(
        input_tpm=80.0,
        output_tpm=120.0,
        total_tpm=200.0,
    )


def test_classify_input_returns_greeting_for_hello() -> None:
    from agent_harness_core.workflows.demo_route import classify_input

    assert classify_input("hello") == "greeting"
    assert classify_input("hi there") == "greeting"
    assert classify_input("good morning") == "greeting"
    assert classify_input("Hey, how are you?") == "greeting"


def test_classify_input_returns_question_for_questions() -> None:
    from agent_harness_core.workflows.demo_route import classify_input

    assert classify_input("what is this?") == "question"
    assert classify_input("how does it work?") == "question"
    assert classify_input("why is the sky blue?") == "question"
    assert classify_input("can you help me?") == "question"


def test_classify_input_returns_command_for_verbs() -> None:
    from agent_harness_core.workflows.demo_route import classify_input

    assert classify_input("run this") == "command"
    assert classify_input("please do it") == "command"
    assert classify_input("start the process") == "command"
    assert classify_input("make a coffee") == "command"


def test_classify_input_returns_statement_for_other_text() -> None:
    from agent_harness_core.workflows.demo_route import classify_input

    assert classify_input("the weather is nice") == "statement"
    assert classify_input("I like programming") == "statement"


def test_choose_tool_returns_calculator_for_math_expressions() -> None:
    from agent_harness_core.workflows.react import choose_tool

    tool, input_val = choose_tool("calculate 2 + 3")
    assert tool == "calculator"
    assert input_val == "2 + 3"

    tool, input_val = choose_tool("what is 10 * 5")
    assert tool == "calculator"
    assert input_val == "10 * 5"


def test_choose_tool_returns_capital_for_country_queries() -> None:
    from agent_harness_core.workflows.react import choose_tool

    tool, input_val = choose_tool("capital of France")
    assert tool == "lookup_capital"
    assert input_val == "France"

    tool, input_val = choose_tool("capital of Japan?")
    assert tool == "lookup_capital"
    assert input_val == "Japan"


def test_choose_tool_returns_count_words_for_word_count_queries() -> None:
    from agent_harness_core.workflows.react import choose_tool

    tool, input_val = choose_tool("count words in hello world")
    assert tool == "count_words"
    assert input_val == "hello world"


def test_choose_tool_returns_none_for_plain_text() -> None:
    from agent_harness_core.workflows.react import choose_tool

    tool, input_val = choose_tool("hello world")
    assert tool is None
    assert input_val is None


def test_lookup_capital_returns_known_capitals() -> None:
    from agent_harness_core.workflows.react import lookup_capital

    assert lookup_capital("france") == "Paris"
    assert lookup_capital("japan") == "Tokyo"
    assert lookup_capital("Thailand") == "Bangkok"


def test_lookup_capital_returns_unknown_for_unknown_countries() -> None:
    from agent_harness_core.workflows.react import lookup_capital

    result = lookup_capital("unknown country")
    assert "Unknown capital" in result


def test_count_words_returns_correct_count() -> None:
    from agent_harness_core.workflows.react import count_words

    assert count_words("hello world") == "2"
    assert count_words("one two three four five") == "5"
    assert count_words("   ") == "0"
    assert count_words("") == "0"


def test_calculate_expression_returns_math_results() -> None:
    from agent_harness_core.workflows.react import calculate_expression

    assert calculate_expression("2 + 3") == "5"
    assert calculate_expression("10 - 4") == "6"
    assert calculate_expression("3 * 4") == "12"
    assert calculate_expression("10 / 2") == "5"
    assert calculate_expression("2 + 3 * 4") == "14"


def test_format_tool_response_different_formats() -> None:
    from agent_harness_core.workflows.react import _format_tool_response

    assert _format_tool_response("calculator", "2 + 3", "5") == "I used calculator on 2 + 3 and got 5."
    assert _format_tool_response("lookup_capital", "france", "Paris") == "I used lookup_capital and found: france -> Paris."
    assert _format_tool_response("count_words", "hello world", "2") == "I used count_words and found 2 words in: hello world"
