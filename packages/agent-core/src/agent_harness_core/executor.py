from __future__ import annotations

from time import perf_counter
from typing import Any, Callable

from langgraph.graph import END, START, StateGraph
from opentelemetry.trace import SpanKind
from typing_extensions import TypedDict

from agent_harness_contracts import RunRecord, RunStatus, TokenUsage
from agent_harness_core.runtime import RunStore
from agent_harness_observability import (
    ServiceObservability,
    bind_log_context,
    build_observability,
    capture_current_trace,
    context_from_traceparent,
    start_span,
)


class WorkflowState(TypedDict):
    user_input: str
    normalized_input: str
    response: str


class ExecutionCancelled(RuntimeError):
    """Raised when a run should stop because cancellation was requested."""


class RuntimeExecutor:
    def __init__(self, store: RunStore, observability: ServiceObservability | None = None) -> None:
        self._store = store
        self._observability = observability or build_observability("agent-core")

    def execute(self, run: RunRecord) -> RunRecord:
        parent_context = context_from_traceparent(run.traceparent)
        with start_span(
            self._observability.tracer,
            "run.execute",
            kind=SpanKind.CONSUMER,
            context=parent_context,
            attributes={
                "agent.run_id": run.run_id,
                "agent.workflow": run.workflow,
                "agent.worker_id": run.worker_id or "unknown",
            },
        ):
            trace_snapshot = capture_current_trace()
            if run.trace_id is None and trace_snapshot.trace_id and trace_snapshot.traceparent:
                run = self._store.set_run_trace_context(
                    run.run_id,
                    trace_snapshot.trace_id,
                    trace_snapshot.traceparent,
                )

            with bind_log_context(run_id=run.run_id, trace_id=trace_snapshot.trace_id):
                self._assert_not_cancelled(run.run_id)
                self._observability.metrics.record_run_started(run)
                with start_span(
                    self._observability.tracer,
                    "workflow.execute",
                    attributes={
                        "agent.run_id": run.run_id,
                        "agent.workflow": run.workflow,
                    },
                ):
                    self._store.append_event(
                        run.run_id,
                        event_type="workflow.started",
                        category="workflow",
                        payload={"workflow": run.workflow},
                    )
                    graph = self._build_graph(run)
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
                completed_run = self._store.mark_run_completed(
                    run.run_id,
                    {
                        "response": result["response"],
                        "normalized_input": result["normalized_input"],
                    },
                )
                self._observability.metrics.record_run_terminal(completed_run)
                return completed_run

    def _build_graph(self, run: RunRecord) -> Any:
        graph = StateGraph(WorkflowState)
        graph.add_node("normalize_input", self._normalize_input(run))
        graph.add_node("generate_response", self._generate_response(run))
        graph.add_edge(START, "normalize_input")
        graph.add_edge("normalize_input", "generate_response")
        graph.add_edge("generate_response", END)
        return graph.compile()

    def _normalize_input(self, run: RunRecord) -> Callable[[WorkflowState], WorkflowState]:
        def node(state: WorkflowState) -> WorkflowState:
            return self._run_node(
                run,
                node_name="normalize_input",
                body=lambda: self._normalize_input_value(run, state),
            )

        return node

    def _generate_response(self, run: RunRecord) -> Callable[[WorkflowState], WorkflowState]:
        def node(state: WorkflowState) -> WorkflowState:
            return self._run_node(
                run,
                node_name="generate_response",
                body=lambda: self._generate_response_value(run, state),
            )

        return node

    def _run_node(
        self,
        run: RunRecord,
        *,
        node_name: str,
        body: Callable[[], WorkflowState],
    ) -> WorkflowState:
        self._assert_not_cancelled(run.run_id)
        started_at = perf_counter()
        outcome = "ok"
        try:
            with start_span(
                self._observability.tracer,
                f"node.{node_name}",
                attributes={
                    "agent.run_id": run.run_id,
                    "agent.workflow": run.workflow,
                    "agent.node_name": node_name,
                },
            ):
                self._store.append_event(
                    run.run_id,
                    event_type="node.started",
                    category="node",
                    node_name=node_name,
                    payload={},
                )
                next_state = body()
                self._store.append_event(
                    run.run_id,
                    event_type="node.completed",
                    category="node",
                    node_name=node_name,
                    payload={
                        key: value
                        for key, value in next_state.items()
                        if key != "user_input"
                    },
                )
                return next_state
        except Exception:
            outcome = "error"
            raise
        finally:
            self._observability.metrics.record_node(
                workflow=run.workflow,
                node_name=node_name,
                outcome=outcome,
                duration_seconds=perf_counter() - started_at,
            )

    def _normalize_input_value(self, run: RunRecord, state: WorkflowState) -> WorkflowState:
        started_at = perf_counter()
        outcome = "ok"
        tool_name = "normalize_whitespace"
        try:
            with start_span(
                self._observability.tracer,
                f"tool.{tool_name}",
                kind=SpanKind.CLIENT,
                attributes={
                    "agent.run_id": run.run_id,
                    "agent.workflow": run.workflow,
                    "agent.node_name": "normalize_input",
                    "agent.tool_name": tool_name,
                },
            ):
                self._store.append_event(
                    run.run_id,
                    event_type="tool.started",
                    category="tool",
                    node_name="normalize_input",
                    tool_name=tool_name,
                    payload={},
                )
                normalized_input = " ".join(state["user_input"].strip().split())
                self._store.append_event(
                    run.run_id,
                    event_type="tool.completed",
                    category="tool",
                    node_name="normalize_input",
                    tool_name=tool_name,
                    payload={"normalized_input": normalized_input},
                )
                return {
                    **state,
                    "normalized_input": normalized_input,
                }
        except Exception:
            outcome = "error"
            raise
        finally:
            self._observability.metrics.record_tool(
                workflow=run.workflow,
                node_name="normalize_input",
                tool_name=tool_name,
                outcome=outcome,
                duration_seconds=perf_counter() - started_at,
            )

    def _generate_response_value(self, run: RunRecord, state: WorkflowState) -> WorkflowState:
        started_at = perf_counter()
        outcome = "ok"
        model_name = "demo-echo-model"
        usage: TokenUsage | None = None
        try:
            with start_span(
                self._observability.tracer,
                f"model.{model_name}",
                kind=SpanKind.CLIENT,
                attributes={
                    "agent.run_id": run.run_id,
                    "agent.workflow": run.workflow,
                    "agent.node_name": "generate_response",
                    "agent.model_name": model_name,
                },
            ):
                self._store.append_event(
                    run.run_id,
                    event_type="model.started",
                    category="model",
                    node_name="generate_response",
                    model_name=model_name,
                    payload={"input": state["normalized_input"]},
                )
                response = f"Echo: {state['normalized_input']}"
                usage = self._build_usage(state["normalized_input"], response)
                self._store.append_event(
                    run.run_id,
                    event_type="model.completed",
                    category="model",
                    node_name="generate_response",
                    model_name=model_name,
                    payload={
                        "response": response,
                        "usage": usage.model_dump(),
                    },
                )
                return {
                    **state,
                    "response": response,
                }
        except Exception:
            outcome = "error"
            raise
        finally:
            self._observability.metrics.record_model(
                workflow=run.workflow,
                node_name="generate_response",
                model_name=model_name,
                outcome=outcome,
                duration_seconds=perf_counter() - started_at,
                usage=usage,
            )

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
