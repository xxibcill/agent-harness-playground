from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from time import perf_counter

from agent_harness_contracts import (
    CancelRunResponse,
    CreateRunRequest,
    CreateRunResponse,
    ListRunsResponse,
    RunStatus,
)
from agent_harness_core import InMemoryRunStore, RunStore, build_run_store
from agent_harness_observability import (
    CorrelationFilter,
    ServiceObservability,
    bind_log_context,
    build_observability,
    capture_current_trace,
    start_span,
)
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from agent_harness_api.auth import RequestAuthorizer, build_authorizer_from_env


def build_logger() -> logging.Logger:
    log_format = (
        "%(asctime)s %(levelname)s %(name)s "
        "run_id=%(run_id)s trace_id=%(trace_id)s %(message)s"
    )
    logger = logging.getLogger("agent_harness_api")
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(log_format))
        handler.addFilter(CorrelationFilter())
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


def build_cors_origins() -> list[str]:
    configured = os.getenv(
        "AGENT_HARNESS_CORS_ORIGINS",
        "http://127.0.0.1:3000,http://localhost:3000",
    )
    return [origin.strip() for origin in configured.split(",") if origin.strip()]


def create_app(
    store: RunStore | None = None,
    observability: ServiceObservability | None = None,
    authorizer: RequestAuthorizer | None = None,
) -> FastAPI:
    runtime_store = store or build_run_store()
    telemetry = observability or build_observability("agent-harness-api")
    request_authorizer = authorizer or build_authorizer_from_env()
    logger = build_logger()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        runtime_store.apply_migrations()
        try:
            yield
        finally:
            telemetry.shutdown()

    app = FastAPI(
        title="Agent Harness API",
        version="0.2.0",
        summary="Control plane for durable agent runs and runtime events.",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=build_cors_origins(),
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.run_store = runtime_store
    app.state.observability = telemetry
    app.state.authorizer = request_authorizer
    app.mount("/metrics", telemetry.metrics_app())

    def get_store() -> RunStore:
        return app.state.run_store

    @app.middleware("http")
    async def instrument_requests(request, call_next):  # type: ignore[no-untyped-def]
        route = request.url.path
        started_at = perf_counter()
        with start_span(
            telemetry.tracer,
            "http.request",
            attributes={
                "http.method": request.method,
                "http.route": route,
            },
        ):
            trace_snapshot = capture_current_trace()
            with bind_log_context(trace_id=trace_snapshot.trace_id):
                try:
                    app.state.authorizer.authorize(request)
                except HTTPException as exc:
                    telemetry.metrics.record_http_request(
                        method=request.method,
                        route=route,
                        status_code=exc.status_code,
                        duration_seconds=perf_counter() - started_at,
                    )
                    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
                response = await call_next(request)
                telemetry.metrics.record_http_request(
                    method=request.method,
                    route=route,
                    status_code=response.status_code,
                    duration_seconds=perf_counter() - started_at,
                )
                logger.info(
                    "request completed method=%s route=%s status_code=%s",
                    request.method,
                    route,
                    response.status_code,
                )
                return response

    @app.get("/health")
    def health(runtime_store: RunStore = Depends(get_store)) -> dict[str, str]:
        if runtime_store is None:  # pragma: no cover - defensive guard
            raise HTTPException(status_code=503, detail="Runtime store is unavailable.")
        return {
            "service": "api",
            "status": "ok",
        }

    @app.post("/runs", response_model=CreateRunResponse, status_code=202)
    def create_run(
        request: CreateRunRequest,
        runtime_store: RunStore = Depends(get_store),
    ) -> CreateRunResponse:
        run = runtime_store.create_run(request)
        telemetry.metrics.record_run_created(run)
        telemetry.metrics.refresh_queue(runtime_store.list_runs())
        with bind_log_context(run_id=run.run_id, trace_id=run.trace_id):
            logger.info("created run workflow=%s", run.workflow)
        return CreateRunResponse(run=run)

    @app.get("/runs", response_model=ListRunsResponse)
    def list_runs(runtime_store: RunStore = Depends(get_store)) -> ListRunsResponse:
        runs = runtime_store.list_runs()
        telemetry.metrics.refresh_queue(runs)
        return ListRunsResponse(runs=runs)

    @app.get("/runs/{run_id}", response_model=CreateRunResponse)
    def get_run(
        run_id: str,
        runtime_store: RunStore = Depends(get_store),
    ) -> CreateRunResponse:
        run = runtime_store.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"Run {run_id} was not found.")
        return CreateRunResponse(run=run)

    @app.post("/runs/{run_id}/cancel", response_model=CancelRunResponse)
    def cancel_run(
        run_id: str,
        runtime_store: RunStore = Depends(get_store),
    ) -> CancelRunResponse:
        run = runtime_store.cancel_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"Run {run_id} was not found.")
        telemetry.metrics.refresh_queue(runtime_store.list_runs())
        if str(run.status) in {
            RunStatus.CANCELLED.value,
            RunStatus.FAILED.value,
            RunStatus.COMPLETED.value,
        }:
            telemetry.metrics.record_run_terminal(run)
        with bind_log_context(run_id=run.run_id, trace_id=run.trace_id):
            logger.info("cancelled or marked run for cancellation status=%s", run.status)
        return CancelRunResponse(run=run)

    @app.get("/runs/{run_id}/events/stream")
    async def stream_run_events(
        run_id: str,
        runtime_store: RunStore = Depends(get_store),
        follow: bool = Query(default=True),
        since_sequence: int = Query(default=0, ge=0),
        poll_interval_seconds: float = Query(default=0.25, gt=0.0, le=5.0),
    ) -> StreamingResponse:
        run = runtime_store.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"Run {run_id} was not found.")

        async def event_stream() -> AsyncIterator[str]:
            last_sequence = since_sequence
            while True:
                events = runtime_store.list_events(run_id, after_sequence=last_sequence)
                for event in events:
                    last_sequence = event.sequence
                    yield f"id: {event.sequence}\n"
                    yield "event: run-event\n"
                    yield f"data: {json.dumps(event.model_dump(mode='json'))}\n\n"

                current = runtime_store.get_run(run_id)
                if current is None:
                    break
                if not follow or current.status in {
                    RunStatus.COMPLETED,
                    RunStatus.FAILED,
                    RunStatus.CANCELLED,
                }:
                    break
                await asyncio.sleep(poll_interval_seconds)

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    return app


app = create_app()


def run() -> None:
    import uvicorn

    uvicorn.run("agent_harness_api.main:app", host="127.0.0.1", port=8000, reload=True)


def create_in_memory_app() -> FastAPI:
    return create_app(store=InMemoryRunStore())
