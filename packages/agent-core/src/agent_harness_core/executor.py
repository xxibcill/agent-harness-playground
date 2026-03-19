from __future__ import annotations

from typing import Any, Callable

from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

from agent_harness_contracts import RunRecord, RunStatus, TokenUsage
from agent_harness_core.runtime import RunStore


class WorkflowState(TypedDict):
    user_input: str
    normalized_input: str
    response: str


class ExecutionCancelled(RuntimeError):
    """Raised when a run should stop because cancellation was requested."""


class RuntimeExecutor:
    def __init__(self, store: RunStore) -> None:
        self._store = store

    def execute(self, run: RunRecord) -> RunRecord:
        self._assert_not_cancelled(run.run_id)
        self._store.append_event(
            run.run_id,
            event_type="workflow.started",
            category="workflow",
            payload={"workflow": run.workflow},
        )
        graph = self._build_graph(run.run_id)
        result = graph.invoke(
            {
                "user_input": run.input,
                "normalized_input": "",
                "response": "",
            }
        )
        self._store.append_event(
            run.run_id,
            event_type="workflow.completed",
            category="workflow",
            payload={"workflow": run.workflow},
        )
        return self._store.mark_run_completed(
            run.run_id,
            {
                "response": result["response"],
                "normalized_input": result["normalized_input"],
            },
        )

    def _build_graph(self, run_id: str) -> Any:
        graph = StateGraph(WorkflowState)
        graph.add_node("normalize_input", self._normalize_input(run_id))
        graph.add_node("generate_response", self._generate_response(run_id))
        graph.add_edge(START, "normalize_input")
        graph.add_edge("normalize_input", "generate_response")
        graph.add_edge("generate_response", END)
        return graph.compile()

    def _normalize_input(self, run_id: str) -> Callable[[WorkflowState], WorkflowState]:
        def node(state: WorkflowState) -> WorkflowState:
            self._assert_not_cancelled(run_id)
            self._store.append_event(
                run_id,
                event_type="node.started",
                category="node",
                node_name="normalize_input",
                payload={},
            )
            self._store.append_event(
                run_id,
                event_type="tool.started",
                category="tool",
                node_name="normalize_input",
                tool_name="normalize_whitespace",
                payload={},
            )
            normalized_input = " ".join(state["user_input"].strip().split())
            self._store.append_event(
                run_id,
                event_type="tool.completed",
                category="tool",
                node_name="normalize_input",
                tool_name="normalize_whitespace",
                payload={"normalized_input": normalized_input},
            )
            self._store.append_event(
                run_id,
                event_type="node.completed",
                category="node",
                node_name="normalize_input",
                payload={"normalized_input": normalized_input},
            )
            return {
                **state,
                "normalized_input": normalized_input,
            }

        return node

    def _generate_response(self, run_id: str) -> Callable[[WorkflowState], WorkflowState]:
        def node(state: WorkflowState) -> WorkflowState:
            self._assert_not_cancelled(run_id)
            self._store.append_event(
                run_id,
                event_type="node.started",
                category="node",
                node_name="generate_response",
                payload={},
            )
            self._store.append_event(
                run_id,
                event_type="model.started",
                category="model",
                node_name="generate_response",
                model_name="demo-echo-model",
                payload={"input": state["normalized_input"]},
            )
            response = f"Echo: {state['normalized_input']}"
            usage = self._build_usage(state["normalized_input"], response)
            self._store.append_event(
                run_id,
                event_type="model.completed",
                category="model",
                node_name="generate_response",
                model_name="demo-echo-model",
                payload={
                    "response": response,
                    "usage": usage.model_dump(),
                },
            )
            self._store.append_event(
                run_id,
                event_type="node.completed",
                category="node",
                node_name="generate_response",
                payload={"response": response},
            )
            return {
                **state,
                "response": response,
            }

        return node

    def _assert_not_cancelled(self, run_id: str) -> None:
        current = self._store.get_run(run_id)
        if current is None:
            raise RuntimeError(f"Run {run_id} no longer exists.")
        if current.status == RunStatus.CANCELLING:
            raise ExecutionCancelled("Cancellation requested while run was executing.")

    def _build_usage(self, user_input: str, response: str) -> TokenUsage:
        input_tokens = len(user_input.split())
        output_tokens = len(response.split())
        return TokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
        )
