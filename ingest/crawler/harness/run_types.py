from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from decimal import Decimal
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from crawler.harness.budget import BudgetExceededError
from crawler.harness.budget import BudgetUsage
from crawler.harness.checkpoint import CheckpointState, JsonValue
from crawler.harness.dispatch import PublishResult, StreamEvent
from crawler.harness.planner import PlannedStep

StepStatus = Literal["succeeded", "skipped", "failed", "budget_exceeded"]
RunStatus = Literal["succeeded", "failed", "budget_exceeded"]


class StepResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    state: dict[str, JsonValue] = Field(default_factory=dict)
    cursor: str | None = None
    turns: int = Field(default=0, ge=0)
    tokens: int = Field(default=0, ge=0)
    usd: Decimal = Field(default=Decimal("0"), ge=Decimal("0"))

    @field_validator("usd", mode="before")
    @classmethod
    def _coerce_usd(cls, value: object) -> object:
        if isinstance(value, float):
            return Decimal(str(value))
        return value


class StepRunResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    step_id: str
    handler: str
    status: StepStatus
    sequence: int
    turns: int = 0
    tokens: int = 0
    usd: Decimal = Decimal("0")
    error: str | None = None
    skipped_reason: str | None = None


class RunResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    workflow: str
    checkpoint_key: str
    status: RunStatus
    steps: tuple[StepRunResult, ...]
    usage: BudgetUsage
    error: str | None = None
    checkpoint: CheckpointState | None = None


class AsyncCheckpointStore(Protocol):
    def load(self, key: str) -> CheckpointState | Awaitable[CheckpointState | None] | None: ...

    def save(self, state: CheckpointState) -> CheckpointState | Awaitable[CheckpointState]: ...


class AsyncEventProducer(Protocol):
    def publish(self, event: StreamEvent) -> PublishResult | Awaitable[PublishResult]: ...


StepHandler = Callable[
    [PlannedStep],
    StepResult | Mapping[str, Any] | Awaitable[StepResult | Mapping[str, Any]],
]


def failure_result(
    step: PlannedStep,
    error: str,
    exc: BaseException | None = None,
) -> StepRunResult:
    tokens, usd = exception_usage(exc) if exc is not None else (0, Decimal("0"))
    return StepRunResult(
        step_id=step.step_id,
        handler=step.handler,
        status="failed",
        sequence=step.sequence,
        tokens=tokens,
        usd=usd,
        error=error,
    )


def budget_result(
    step: PlannedStep,
    exc: BudgetExceededError,
    step_result: StepResult | None = None,
    *,
    tokens: int | None = None,
    usd: Decimal | None = None,
) -> StepRunResult:
    tokens = tokens if tokens is not None else (step_result.tokens if step_result is not None else 0)
    turns = step_result.turns if step_result is not None else 0
    usd = usd if usd is not None else (step_result.usd if step_result is not None else Decimal("0"))
    return StepRunResult(
        step_id=step.step_id,
        handler=step.handler,
        status="budget_exceeded",
        sequence=step.sequence,
        turns=turns,
        tokens=tokens,
        usd=usd,
        error=str(exc),
    )


def exception_usage(exc: BaseException | None) -> tuple[int, Decimal]:
    if exc is None:
        return 0, Decimal("0")
    tokens = getattr(exc, "usage_tokens", 0)
    usd = getattr(exc, "usage_usd", Decimal("0"))
    return (tokens if isinstance(tokens, int) and tokens >= 0 else 0, Decimal(str(usd)))


def highest_completed_sequence(metadata: dict[str, JsonValue]) -> int:
    highest = 0
    completed_steps = metadata.get("completed_steps")
    completed_values = completed_steps.values() if isinstance(completed_steps, dict) else ()
    for completed in completed_values:
        if isinstance(completed, dict):
            sequence = completed.get("sequence")
            if isinstance(sequence, int):
                highest = max(highest, sequence)
    return highest
