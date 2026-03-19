from __future__ import annotations

from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(
        title="Agent Harness API",
        version="0.1.0",
        summary="Control plane for agent runs and monitoring queries.",
    )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {
            "service": "api",
            "status": "ok",
        }

    @app.get("/runs")
    def list_runs() -> dict[str, list[dict[str, str]]]:
        return {"runs": []}

    return app


app = create_app()


def run() -> None:
    import uvicorn

    uvicorn.run("agent_harness_api.main:app", host="127.0.0.1", port=8000, reload=True)

