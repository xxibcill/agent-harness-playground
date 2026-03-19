# Basic LangGraph Agent

This project starts with a minimal LangGraph agent: one state graph, one Anthropic-backed node, and one CLI command.

## Requirements

- Python 3.11+
- `uv`

## Install

```bash
uv sync
```

## Environment

The agent now loads `.env` automatically from the project root. Put your Anthropic-compatible endpoint settings there:

```bash
cp .env.example .env
```

`ANTHROPIC_MODEL` must be a model ID accepted by your endpoint.

## First run

```bash
uv run basic-agent --model "your_provider_model" "Say hello to LangGraph"
```

If `.env` already includes `ANTHROPIC_MODEL`, you can omit `--model`.

## Run tests

```bash
uv run pytest
```

## Notes

The agent reads `ANTHROPIC_AUTH_TOKEN` first and falls back to `ANTHROPIC_API_KEY` if needed. Shell environment variables still win over `.env`, and `API_TIMEOUT_MS` is converted from milliseconds to seconds for the Anthropic Python SDK.
