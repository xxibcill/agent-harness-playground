from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    CANCELLING = "cancelling"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in {
            RunStatus.COMPLETED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }


class TokenUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


class CreateRunRequest(BaseModel):
    workflow: str = Field(default="demo.echo", min_length=1)
    input: str = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)
    scheduled_at: str | None = None


class RunRecord(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    run_id: str
    workflow: str
    status: RunStatus
    input: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] | None = None
    error: str | None = None
    scheduled_at: str
    created_at: str
    updated_at: str
    started_at: str | None = None
    completed_at: str | None = None
    cancel_requested_at: str | None = None
    attempt_count: int = 0
    worker_id: str | None = None
    lease_expires_at: str | None = None
    trace_id: str | None = None
    traceparent: str | None = None


class RunEvent(BaseModel):
    event_id: str
    run_id: str
    sequence: int
    event_type: str
    category: str
    created_at: str
    node_name: str | None = None
    tool_name: str | None = None
    model_name: str | None = None
    trace_id: str | None = None
    span_id: str | None = None
    parent_span_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class CreateRunResponse(BaseModel):
    run: RunRecord


class ListRunsResponse(BaseModel):
    runs: list[RunRecord]


class CancelRunResponse(BaseModel):
    run: RunRecord
