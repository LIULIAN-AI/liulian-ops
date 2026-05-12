from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal, cast
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from crawler.api.dependencies import ObservabilityDep, ProducerDep, RunRegistryDep
from crawler.harness.checkpoint import JsonValue
from crawler.harness.dispatch import PublishResult, StreamEvent
from crawler.harness.distill import redact_json
from crawler.harness.planner import WorkflowName
from crawler.harness.workflows import trigger_workflow_async
from crawler.harness.workflows import workflow_trigger_event

router = APIRouter(prefix="/admin/runs", tags=["runs"])

_WORKFLOWS: set[str] = {
    "discover_banks",
    "refresh_company",
    "refresh_field_group",
    "re_extract_changed_snapshots",
}


class TriggerRunRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    checkpoint_key: str | None = Field(default=None, min_length=1)
    requested_by: str = Field(default="admin", min_length=1)
    params: dict[str, JsonValue] = Field(default_factory=dict)


class RunSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: UUID
    workflow: WorkflowName
    checkpoint_key: str
    requested_by: str
    requested_at: datetime
    status: Literal["queued", "duplicate"]
    stream: str
    stream_id: str | None
    dedup_key: str | None
    params: dict[str, JsonValue]
    step_count: int


class TriggerRunResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    run: RunSummary


class ListRunsResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    runs: tuple[RunSummary, ...]


@router.post("/trigger", response_model=TriggerRunResponse)
async def trigger_run(
    producer: ProducerDep,
    observability_sink: ObservabilityDep,
    run_registry: RunRegistryDep,
    workflow: str = Query(..., min_length=1),
    request: TriggerRunRequest | None = None,
) -> TriggerRunResponse:
    workflow_name = _workflow_name(workflow)
    body = request or TriggerRunRequest()
    requested_at = datetime.now(UTC)
    event = workflow_trigger_event(
        workflow_name,
        checkpoint_key=body.checkpoint_key,
        requested_by=body.requested_by,
        at=requested_at,
        **body.params,
    )
    result = await trigger_workflow_async(
        producer,
        workflow_name,
        checkpoint_key=body.checkpoint_key,
        requested_by=body.requested_by,
        at=requested_at,
        **body.params,
    )
    if result.duplicate:
        existing = run_registry.find(dedup_key=result.dedup_key)
        if existing is not None:
            event = existing[0]
    else:
        run_registry.record(event, result)
    summary = _summary_from_event(event, result)
    observability_sink.record_langfuse_event(
        "control_plane.run_triggered",
        input={},
        output={
            "workflow": summary.workflow,
            "checkpoint_key": summary.checkpoint_key,
            "duplicate": result.duplicate,
        },
        metadata={"requested_by": summary.requested_by},
    )
    return TriggerRunResponse(run=summary)


@router.get("", response_model=ListRunsResponse)
async def list_runs(
    run_registry: RunRegistryDep,
    limit: int = Query(default=50, ge=1, le=200),
) -> ListRunsResponse:
    return ListRunsResponse(
        runs=tuple(
            _summary_from_event(event, result)
            for event, result in run_registry.latest(limit=limit)
        )
    )


def _workflow_name(value: str) -> WorkflowName:
    if value not in _WORKFLOWS:
        raise HTTPException(status_code=422, detail=f"Unsupported workflow: {value}")
    return cast(WorkflowName, value)


def _summary_from_event(event: StreamEvent, result: PublishResult) -> RunSummary:
    payload = event.payload
    return RunSummary(
        run_id=event.event_id,
        workflow=cast(WorkflowName, payload["workflow"]),
        checkpoint_key=str(payload["checkpoint_key"]),
        requested_by=str(payload["requested_by"]),
        requested_at=datetime.fromisoformat(str(payload["requested_at"])),
        status="duplicate" if result.duplicate else "queued",
        stream=result.stream,
        stream_id=result.stream_id,
        dedup_key=result.dedup_key,
        params=redact_json(cast(dict[str, JsonValue], payload.get("params", {}))),
        step_count=len(cast(list[JsonValue], payload.get("steps", []))),
    )
