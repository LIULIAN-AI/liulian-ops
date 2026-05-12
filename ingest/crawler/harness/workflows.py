from __future__ import annotations

from collections.abc import Awaitable
from datetime import UTC, datetime
from hashlib import sha256
from inspect import isawaitable
from typing import Literal, Protocol, TypeVar, cast
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field

from crawler.agents.source_selector import SourceDefinition, SourceRegistry
from crawler.harness.checkpoint import JsonValue
from crawler.harness.dispatch import PublishResult, StreamEvent, StreamProducer
from crawler.harness.distill import redact_json
from crawler.harness.planner import PlannedStep, WorkflowName, plan_workflow

Cadence = Literal["manual", "hourly", "daily", "weekly", "monthly", "quarterly"]
DAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
T = TypeVar("T")


class AsyncStreamProducer(Protocol):
    def publish(self, event: StreamEvent) -> PublishResult | Awaitable[PublishResult]: ...


class CronSchedule(BaseModel):
    model_config = ConfigDict(frozen=True)

    trigger: Literal["cron"] = "cron"
    minute: str = "*"
    hour: str | None = None
    day: str | None = None
    day_of_week: str | None = None
    month: str | None = None
    timezone: str = "UTC"

    def apscheduler_kwargs(self) -> dict[str, str]:
        data = self.model_dump(exclude_none=True)
        return {key: str(value) for key, value in data.items()}


class ScheduleSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    schedule_id: str = Field(min_length=1)
    target_type: Literal["source", "workflow"]
    target_id: str = Field(min_length=1)
    cadence: Cadence
    cron: CronSchedule | None = None

    @property
    def apscheduler_cron(self) -> dict[str, str] | None:
        return None if self.cron is None else self.cron.apscheduler_kwargs()


def schedule_for_source(source: SourceDefinition, *, timezone: str = "UTC") -> ScheduleSpec:
    cadence = _coerce_cadence(source.cadence)
    return ScheduleSpec(
        schedule_id=f"source:{source.id}:{cadence}",
        target_type="source",
        target_id=source.id,
        cadence=cadence,
        cron=cron_for_cadence(cadence, key=f"source:{source.id}", timezone=timezone),
    )


def schedules_for_sources(
    registry: SourceRegistry,
    *,
    timezone: str = "UTC",
) -> tuple[ScheduleSpec, ...]:
    return tuple(
        schedule_for_source(source, timezone=timezone)
        for source in registry.sources
        if source.enabled
    )


def schedule_for_workflow(
    workflow: WorkflowName,
    *,
    cadence: Cadence | None = None,
    timezone: str = "UTC",
) -> ScheduleSpec:
    resolved = cadence or _default_workflow_cadence(workflow)
    return ScheduleSpec(
        schedule_id=f"workflow:{workflow}:{resolved}",
        target_type="workflow",
        target_id=workflow,
        cadence=resolved,
        cron=cron_for_cadence(resolved, key=f"workflow:{workflow}", timezone=timezone),
    )


def default_workflow_schedules(*, timezone: str = "UTC") -> tuple[ScheduleSpec, ...]:
    workflows: tuple[WorkflowName, ...] = (
        "discover_banks",
        "refresh_company",
        "refresh_field_group",
        "re_extract_changed_snapshots",
    )
    return tuple(schedule_for_workflow(workflow, timezone=timezone) for workflow in workflows)


def cron_for_cadence(cadence: Cadence, *, key: str, timezone: str = "UTC") -> CronSchedule | None:
    if cadence == "manual":
        return None
    minute = str(_slot(f"{key}:minute", 60))
    if cadence == "hourly":
        return CronSchedule(minute=minute, timezone=timezone)
    hour = str(_slot(f"{key}:hour", 24))
    if cadence == "daily":
        return CronSchedule(minute=minute, hour=hour, timezone=timezone)
    if cadence == "weekly":
        return CronSchedule(
            minute=minute,
            hour=hour,
            day_of_week=DAY_NAMES[_slot(f"{key}:dow", 7)],
            timezone=timezone,
        )
    day = str(_slot(f"{key}:day", 28) + 1)
    if cadence == "monthly":
        return CronSchedule(minute=minute, hour=hour, day=day, timezone=timezone)
    return CronSchedule(minute=minute, hour=hour, day=day, month="1,4,7,10", timezone=timezone)


def trigger_workflow(
    producer: StreamProducer,
    workflow: WorkflowName,
    *,
    checkpoint_key: str | None = None,
    requested_by: str = "manual",
    at: datetime | None = None,
    **params: JsonValue,
) -> PublishResult:
    return producer.publish(
        workflow_trigger_event(
            workflow,
            checkpoint_key=checkpoint_key,
            requested_by=requested_by,
            at=at,
            **params,
        )
    )


async def trigger_workflow_async(
    producer: AsyncStreamProducer,
    workflow: WorkflowName,
    *,
    checkpoint_key: str | None = None,
    requested_by: str = "manual",
    at: datetime | None = None,
    **params: JsonValue,
) -> PublishResult:
    return await _maybe_await(
        producer.publish(
            workflow_trigger_event(
                workflow,
                checkpoint_key=checkpoint_key,
                requested_by=requested_by,
                at=at,
                **params,
            )
        )
    )


def workflow_trigger_event(
    workflow: WorkflowName,
    *,
    checkpoint_key: str | None = None,
    requested_by: str = "manual",
    at: datetime | None = None,
    **params: JsonValue,
) -> StreamEvent:
    plan = plan_workflow(workflow, checkpoint_key=checkpoint_key, **params)
    clean_params: dict[str, JsonValue] = {
        key: value for key, value in params.items() if value is not None
    }
    dedup_key = f"workflow:{workflow}:{plan.checkpoint_key}:requested"
    requested_at = at or datetime.now(UTC)
    payload: dict[str, JsonValue] = {
        "workflow": workflow,
        "checkpoint_key": plan.checkpoint_key,
        "requested_by": requested_by,
        "requested_at": requested_at.isoformat(),
        "params": redact_json(clean_params),
        "steps": [_step_payload(step) for step in plan.steps],
    }
    return StreamEvent(
        event_type="workflow.requested",
        payload=payload,
        dedup_key=dedup_key,
        event_id=_deterministic_uuid(dedup_key),
        created_at=requested_at,
    )


def _step_payload(step: PlannedStep) -> dict[str, JsonValue]:
    return {
        "step_id": step.step_id,
        "workflow": step.workflow,
        "handler": step.handler,
        "params": redact_json(step.params),
        "depends_on": list(step.depends_on),
        "dedup_key": step.dedup_key,
        "sequence": step.sequence,
    }


def _default_workflow_cadence(workflow: WorkflowName) -> Cadence:
    if workflow == "discover_banks":
        return "daily"
    if workflow == "re_extract_changed_snapshots":
        return "hourly"
    return "manual"


def _coerce_cadence(value: str) -> Cadence:
    allowed = {"manual", "hourly", "daily", "weekly", "monthly", "quarterly"}
    if value not in allowed:
        raise ValueError(f"Unsupported cadence: {value!r}")
    return cast(Cadence, value)


def _slot(key: str, modulo: int) -> int:
    return int(sha256(key.encode("utf-8")).hexdigest()[:8], 16) % modulo


def _deterministic_uuid(key: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"neobanker-crawler:{key}")


async def _maybe_await(value: T | Awaitable[T]) -> T:
    if isawaitable(value):
        return await value
    return value
