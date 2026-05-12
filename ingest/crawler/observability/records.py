from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, field_validator

from crawler.harness.checkpoint import JsonValue
from crawler.harness.distill import redact_json


class TraceRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    trace_id: UUID
    name: str = Field(min_length=1)
    attributes: dict[str, JsonValue] = Field(default_factory=dict)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class MetricRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1)
    value: Decimal
    unit: str = Field(default="count", min_length=1)
    attributes: dict[str, JsonValue] = Field(default_factory=dict)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("value", mode="before")
    @classmethod
    def _coerce_value(cls, value: object) -> object:
        if isinstance(value, float):
            return Decimal(str(value))
        return value


class LangfuseEventRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_id: UUID
    trace_id: UUID | None = None
    name: str = Field(min_length=1)
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    input: dict[str, JsonValue] = Field(default_factory=dict)
    output: dict[str, JsonValue] = Field(default_factory=dict)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class InMemoryObservabilitySink:
    def __init__(self) -> None:
        self._traces: list[TraceRecord] = []
        self._metrics: list[MetricRecord] = []
        self._langfuse_events: list[LangfuseEventRecord] = []

    @property
    def traces(self) -> tuple[TraceRecord, ...]:
        return tuple(self._traces)

    @property
    def metrics(self) -> tuple[MetricRecord, ...]:
        return tuple(self._metrics)

    @property
    def langfuse_events(self) -> tuple[LangfuseEventRecord, ...]:
        return tuple(self._langfuse_events)

    def record_trace(
        self,
        name: str,
        *,
        attributes: dict[str, JsonValue] | None = None,
        trace_id: UUID | None = None,
        occurred_at: datetime | None = None,
    ) -> TraceRecord:
        record = TraceRecord(
            trace_id=trace_id or _stable_uuid("trace", name, attributes or {}),
            name=name,
            attributes=redact_json(attributes or {}),
            occurred_at=occurred_at or datetime.now(UTC),
        )
        self._traces.append(record)
        return record

    def record_metric(
        self,
        name: str,
        value: Decimal | int | str,
        *,
        unit: str = "count",
        attributes: dict[str, JsonValue] | None = None,
        occurred_at: datetime | None = None,
    ) -> MetricRecord:
        record = MetricRecord(
            name=name,
            value=Decimal(str(value)),
            unit=unit,
            attributes=redact_json(attributes or {}),
            occurred_at=occurred_at or datetime.now(UTC),
        )
        self._metrics.append(record)
        return record

    def record_langfuse_event(
        self,
        name: str,
        *,
        trace_id: UUID | None = None,
        level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO",
        input: dict[str, JsonValue] | None = None,
        output: dict[str, JsonValue] | None = None,
        metadata: dict[str, JsonValue] | None = None,
        occurred_at: datetime | None = None,
    ) -> LangfuseEventRecord:
        record = LangfuseEventRecord(
            event_id=_stable_uuid(
                "langfuse",
                name,
                {
                    "trace_id": str(trace_id) if trace_id is not None else None,
                    "level": level,
                    "input": redact_json(input or {}),
                    "output": redact_json(output or {}),
                    "metadata": redact_json(metadata or {}),
                    "occurred_at": (occurred_at or datetime.now(UTC)).isoformat(),
                },
            ),
            trace_id=trace_id,
            name=name,
            level=level,
            input=redact_json(input or {}),
            output=redact_json(output or {}),
            metadata=redact_json(metadata or {}),
            occurred_at=occurred_at or datetime.now(UTC),
        )
        self._langfuse_events.append(record)
        return record


def _stable_uuid(kind: str, name: str, payload: dict[str, JsonValue]) -> UUID:
    from json import dumps

    canonical = dumps(payload, sort_keys=True, separators=(",", ":"))
    return uuid5(NAMESPACE_URL, f"neobanker-crawler:{kind}:{name}:{canonical}")
