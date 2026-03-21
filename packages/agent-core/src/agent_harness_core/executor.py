from __future__ import annotations

from datetime import timedelta
from time import perf_counter
from typing import Any, Callable, cast

from agent_harness_contracts import RunRecord, RunStatus, TokenUsage
from agent_harness_observability import (
    ServiceObservability,
    bind_log_context,
    build_observability,
    capture_current_trace,
    context_from_traceparent,
    start_span,
)
from opentelemetry.trace import SpanKind

from agent_harness_core.errors import ConfigurationError, ProviderError
from agent_harness_core.runtime import RunStore, parse_datetime, utc_now
from agent_harness_core.workflows import (
    WorkflowDefinition,
    WorkflowRegistry,
    WorkflowResponse,
    WorkflowState,
    build_default_workflow_registry,
    compile_workflow_graph,
)


class ExecutionCancelled(RuntimeError):
    """Raised when a run should stop because cancellation was requested."""


class ExecutionTimedOut(TimeoutError):
    """Raised when a run exceeds its configured timeout."""


class RuntimeExecutor:
    def __init__(
        self,
        store: RunStore,
        observability: ServiceObservability | None = None,
        workflow_registry: WorkflowRegistry | None = None,
    ) -> None:
        self._store = store
        self._observability = observability or build_observability("agent-core")
        self._workflow_registry = workflow_registry or build_default_workflow_registry()

    def execute(self, run: RunRecord) -> RunRecord:
        deadline = self._build_deadline(run)
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
                self._assert_active(run.run_id, deadline)
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
                        payload={
                            "workflow": run.workflow,
                            "attempt_count": run.attempt_count,
                            "max_attempts": run.max_attempts,
                            "timeout_seconds": run.timeout_seconds,
                            "input_summary": self._summarize_text(run.input),
                            "workflow_config": run.workflow_config.model_dump(
                                mode="json",
                                exclude_none=True,
                            ),
                        },
                    )
                    result = self._invoke_workflow_graph(run, deadline)
                    self._assert_active(run.run_id, deadline)
                    self._store.append_event(
                        run.run_id,
                        event_type="workflow.completed",
                        category="workflow",
                        payload={
                            "workflow": run.workflow,
                            "attempt_count": run.attempt_count,
                            "output_summary": self._summarize_text(result["response"]),
                            "state_summary": self._summarize_state(result),
                        },
                    )
                completed_run = self._store.mark_run_completed(
                    run.run_id,
                    self._build_run_output(result),
                )
                self._observability.metrics.record_run_terminal(completed_run)
                return completed_run

    def _invoke_workflow_graph(self, run: RunRecord, deadline) -> WorkflowState:  # type: ignore[no-untyped-def]
        self._assert_active(run.run_id, deadline)
        try:
            graph = self._build_graph(run)
        except ConfigurationError as exc:
            self._append_model_failed_event(
                run,
                model_name=self._model_name_for_failure(run),
                error_type="configuration_error",
                error_message=str(exc),
                recovery_hint="Fix the workflow configuration and submit the run again.",
                config_field=exc.config_field,
            )
            raise

        return cast(
            WorkflowState,
            graph.invoke(
                {
                    "user_input": run.input,
                    "normalized_input": "",
                    "response": "",
                    "model_output": None,
                }
            ),
        )

    def _build_graph(self, run: RunRecord) -> Any:
        workflow = self._workflow_registry.get(run.workflow, run)
        return compile_workflow_graph(
            cast(Any, self._normalize_input(run, workflow)),
            cast(Any, self._generate_response(run, workflow)),
        )

    def _normalize_input(
        self, run: RunRecord, workflow: WorkflowDefinition
    ) -> Callable[[WorkflowState], WorkflowState]:
        def node(state: WorkflowState) -> WorkflowState:
            return self._run_node(
                run,
                node_name="normalize_input",
                state=state,
                body=lambda: self._normalize_input_value(run, workflow, state),
            )

        return node

    def _generate_response(
        self, run: RunRecord, workflow: WorkflowDefinition
    ) -> Callable[[WorkflowState], WorkflowState]:
        def node(state: WorkflowState) -> WorkflowState:
            return self._run_node(
                run,
                node_name="generate_response",
                state=state,
                body=lambda: self._generate_response_value(run, workflow, state),
            )

        return node

    def _run_node(
        self,
        run: RunRecord,
        *,
        node_name: str,
        state: WorkflowState,
        body: Callable[[], WorkflowState],
    ) -> WorkflowState:
        deadline = self._build_deadline(run)
        self._assert_active(run.run_id, deadline)
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
                    payload={
                        "attempt_count": run.attempt_count,
                        "state_summary": self._summarize_state(state),
                    },
                )
                self._assert_active(run.run_id, deadline)
                next_state = body()
                self._assert_active(run.run_id, deadline)
                self._store.append_event(
                    run.run_id,
                    event_type="node.completed",
                    category="node",
                    node_name=node_name,
                    payload=self._build_node_completed_payload(state, next_state),
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

    def _normalize_input_value(
        self,
        run: RunRecord,
        workflow: WorkflowDefinition,
        state: WorkflowState,
    ) -> WorkflowState:
        deadline = self._build_deadline(run)
        self._assert_active(run.run_id, deadline)
        started_at = perf_counter()
        outcome = "ok"
        tool_name = workflow.normalize_tool_name
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
                    payload={
                        "attempt_count": run.attempt_count,
                        "input_summary": self._summarize_text(state["user_input"]),
                    },
                )
                self._assert_active(run.run_id, deadline)
                normalized_input = workflow.normalize_input(state["user_input"])
                self._assert_active(run.run_id, deadline)
                self._store.append_event(
                    run.run_id,
                    event_type="tool.completed",
                    category="tool",
                    node_name="normalize_input",
                    tool_name=tool_name,
                    payload={
                        "normalized_input": normalized_input,
                        "input_summary": self._summarize_text(state["user_input"]),
                        "output_summary": self._summarize_text(normalized_input),
                        "changed": normalized_input != state["user_input"],
                    },
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

    def _generate_response_value(
        self,
        run: RunRecord,
        workflow: WorkflowDefinition,
        state: WorkflowState,
    ) -> WorkflowState:
        deadline = self._build_deadline(run)
        self._assert_active(run.run_id, deadline)
        started_at = perf_counter()
        outcome = "ok"
        model_name = workflow.model_name_hint or f"{run.workflow}-model"
        provider = self._resolve_model_provider(run, model_name)
        workflow_result: WorkflowResponse | None = None
        usage: TokenUsage | None = None
        latency_ms: int | None = None
        request_id: str | None = None
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
                    payload={
                        "input": state["normalized_input"],
                        "input_summary": self._summarize_text(state["normalized_input"]),
                        "provider": provider,
                        "attempt_count": run.attempt_count,
                    },
                )
                self._assert_active(run.run_id, deadline)
                workflow_result = workflow.generate_response(state["normalized_input"])
                model_name = workflow_result.model_name
                provider = self._resolve_model_provider(run, model_name)
                response = workflow_result.response
                usage = workflow_result.usage or self._build_usage(
                    state["normalized_input"], response
                )
                latency_ms = workflow_result.latency_ms
                request_id = workflow_result.request_id
                self._assert_active(run.run_id, deadline)
                model_output = self._build_model_output(
                    run,
                    model_name=model_name,
                    usage=usage,
                    latency_ms=latency_ms,
                    request_id=request_id,
                )
                payload: dict[str, Any] = {
                    "response": response,
                    "response_summary": self._summarize_text(response),
                    **model_output,
                }
                self._store.append_event(
                    run.run_id,
                    event_type="model.completed",
                    category="model",
                    node_name="generate_response",
                    model_name=model_name,
                    payload=payload,
                )
                return {
                    **state,
                    "response": response,
                    "model_output": model_output,
                }
        except ExecutionTimedOut as exc:
            outcome = "error"
            self._append_model_failed_event(
                run,
                model_name=model_name,
                error_type="run_timeout_exceeded",
                error_message=str(exc),
                recovery_hint=(
                    "Increase timeout_seconds for the run or reduce the work done by the "
                    "selected workflow."
                ),
                latency_ms=latency_ms,
                request_id=request_id,
            )
            raise
        except ProviderError as exc:
            outcome = "error"
            self._append_model_failed_event(
                run,
                model_name=model_name,
                error_type=exc.error_type,
                error_message=str(exc),
                recovery_hint=exc.recovery_hint,
                latency_ms=latency_ms,
                request_id=request_id,
            )
            raise
        except ConfigurationError as exc:
            outcome = "error"
            self._append_model_failed_event(
                run,
                model_name=model_name,
                error_type="configuration_error",
                error_message=str(exc),
                recovery_hint="Fix the workflow configuration and submit the run again.",
                config_field=exc.config_field,
                latency_ms=latency_ms,
                request_id=request_id,
            )
            raise
        except Exception as exc:
            outcome = "error"
            self._append_model_failed_event(
                run,
                model_name=model_name,
                error_type="unknown_error",
                error_message=str(exc),
                error_type_name=type(exc).__name__,
                latency_ms=latency_ms,
                request_id=request_id,
            )
            raise
        finally:
            if workflow_result is not None:
                model_name = workflow_result.model_name
            self._observability.metrics.record_model(
                workflow=run.workflow,
                node_name="generate_response",
                model_name=model_name,
                outcome=outcome,
                duration_seconds=perf_counter() - started_at,
                usage=usage,
            )

    def _assert_active(self, run_id: str, deadline) -> None:  # type: ignore[no-untyped-def]
        self._assert_not_cancelled(run_id)
        self._assert_within_deadline(run_id, deadline)

    def _assert_not_cancelled(self, run_id: str) -> None:
        current = self._store.get_run(run_id)
        if current is None:
            raise RuntimeError(f"Run {run_id} no longer exists.")
        if current.status == RunStatus.CANCELLING:
            raise ExecutionCancelled("Cancellation requested while run was executing.")

    def _assert_within_deadline(self, run_id: str, deadline) -> None:  # type: ignore[no-untyped-def]
        if utc_now() > deadline:
            raise ExecutionTimedOut(f"Run {run_id} exceeded its timeout budget.")

    def _build_deadline(self, run: RunRecord):
        started_at = parse_datetime(run.started_at) or utc_now()
        return started_at + timedelta(seconds=run.timeout_seconds)

    def _build_usage(self, user_input: str, response: str) -> TokenUsage:
        input_tokens = len(user_input.split())
        output_tokens = len(response.split())
        return TokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
        )

    def _build_run_output(self, result: WorkflowState) -> dict[str, Any]:
        output: dict[str, Any] = {
            "response": result["response"],
            "normalized_input": result["normalized_input"],
        }
        if result.get("model_output") is not None:
            output["model"] = result["model_output"]
        return output

    def _build_node_completed_payload(
        self, previous_state: WorkflowState, next_state: WorkflowState
    ) -> dict[str, Any]:
        payload = {
            key: value
            for key, value in next_state.items()
            if key not in {"user_input", "model_output"}
        }
        payload["state_summary"] = self._summarize_state(next_state)
        payload["state_diff"] = self._build_state_diff(previous_state, next_state)
        if next_state.get("model_output") is not None:
            payload["model"] = next_state["model_output"]
        return payload

    def _summarize_state(self, state: WorkflowState) -> dict[str, Any]:
        return {
            key: self._summarize_value(value)
            for key, value in state.items()
            if value not in (None, "")
        }

    def _build_state_diff(
        self, previous_state: WorkflowState, next_state: WorkflowState
    ) -> dict[str, list[str]]:
        previous_keys = set(previous_state)
        next_keys = set(next_state)
        return {
            "added": sorted(next_keys - previous_keys),
            "removed": sorted(previous_keys - next_keys),
            "updated": sorted(
                key for key in previous_keys & next_keys if previous_state[key] != next_state[key]
            ),
        }

    def _summarize_value(self, value: Any) -> Any:
        if isinstance(value, str):
            return self._summarize_text(value)
        if isinstance(value, dict):
            return {
                "type": "object",
                "keys": sorted(str(key) for key in value),
            }
        if isinstance(value, list):
            return {
                "type": "array",
                "length": len(value),
            }
        return value

    def _summarize_text(self, value: str, *, limit: int = 160) -> dict[str, Any]:
        preview = value if len(value) <= limit else f"{value[: limit - 3]}..."
        return {
            "preview": preview,
            "chars": len(value),
            "words": len(value.split()),
            "truncated": len(preview) != len(value),
        }

    def _append_model_failed_event(
        self,
        run: RunRecord,
        *,
        model_name: str,
        error_type: str,
        error_message: str,
        recovery_hint: str | None = None,
        config_field: str | None = None,
        error_type_name: str | None = None,
        latency_ms: int | None = None,
        request_id: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "provider": self._resolve_model_provider(run, model_name),
            "error_type": error_type,
            "error_message": error_message,
        }
        if recovery_hint is not None:
            payload["recovery_hint"] = recovery_hint
        if config_field is not None:
            payload["config_field"] = config_field
        if error_type_name is not None:
            payload["error_type_name"] = error_type_name
        if latency_ms is not None:
            payload["latency_ms"] = latency_ms
        if request_id is not None:
            payload["request_id"] = request_id

        self._store.append_event(
            run.run_id,
            event_type="model.failed",
            category="model",
            node_name="generate_response",
            model_name=model_name,
            payload=payload,
        )

    def _build_model_output(
        self,
        run: RunRecord,
        *,
        model_name: str,
        usage: TokenUsage | None,
        latency_ms: int | None,
        request_id: str | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "provider": self._resolve_model_provider(run, model_name),
            "model_name": model_name,
            "usage": usage.model_dump() if usage is not None else {},
        }
        if latency_ms is not None:
            payload["latency_ms"] = latency_ms
        if request_id is not None:
            payload["request_id"] = request_id
        return payload

    def _resolve_model_provider(self, run: RunRecord, model_name: str) -> str:
        if run.workflow_config.provider is not None:
            return str(run.workflow_config.provider)
        if model_name.startswith("demo-"):
            return "demo"
        workflow_prefix = run.workflow.split(".", maxsplit=1)[0]
        return workflow_prefix or "unknown"

    def _model_name_for_failure(self, run: RunRecord) -> str:
        configured_model = run.workflow_config.model
        if configured_model:
            return configured_model
        return f"{run.workflow}-model"
