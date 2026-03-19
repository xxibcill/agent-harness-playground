# Basic LangGraph Agent

This project starts with the smallest useful LangGraph setup: one state graph, one node, and one CLI command.

## Requirements

- Python 3.11+
- `uv`

## Install

```bash
uv sync
```

## First run

```bash
uv run basic-agent "Say hello to LangGraph"
```

Expected output:

```text
Hello from LangGraph. You said: Say hello to LangGraph
```

## Run tests

```bash
uv run pytest
```

## Next step

When you want a real LLM-backed agent, replace the `respond` node in `src/basic_langgraph_agent/agent.py` with a model call and keep the surrounding graph unchanged.
