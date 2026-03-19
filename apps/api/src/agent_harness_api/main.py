from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse

from agent_harness_contracts import (
    CancelRunResponse,
    CreateRunRequest,
    CreateRunResponse,
    ListRunsResponse,
    RunStatus,
)
from agent_harness_core import InMemoryRunStore, RunStore, build_run_store


def create_app(store: RunStore | None = None) -> FastAPI:
    runtime_store = store or build_run_store()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        runtime_store.apply_migrations()
        yield

    app = FastAPI(
        title="Agent Harness API",
        version="0.2.0",
        summary="Control plane for durable agent runs and runtime events.",
        lifespan=lifespan,
    )
    app.state.run_store = runtime_store

    def get_store() -> RunStore:
        return app.state.run_store

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
        return CreateRunResponse(run=run)

    @app.get("/runs", response_model=ListRunsResponse)
    def list_runs(runtime_store: RunStore = Depends(get_store)) -> ListRunsResponse:
        return ListRunsResponse(runs=runtime_store.list_runs())

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
