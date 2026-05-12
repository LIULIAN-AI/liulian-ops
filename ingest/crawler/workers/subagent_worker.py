from __future__ import annotations

from collections.abc import Awaitable, Mapping
from decimal import Decimal
from inspect import isawaitable
from typing import Literal, TypeVar, cast

from pydantic import BaseModel, ConfigDict, Field

from crawler.agents.base import SubAgent
from crawler.harness.checkpoint import JsonValue
from crawler.harness.distill import redact_json
from crawler.harness.planner import PlannedStep, WorkflowName
from crawler.harness.run_types import StepResult

T = TypeVar("T")
WorkerStatus = Literal["succeeded", "failed", "unknown_handler"]


class SubAgentJob(BaseModel):
    model_config = ConfigDict(frozen=True)

    workflow: WorkflowName
    handler: str = Field(min_length=1)
    params: dict[str, JsonValue] = Field(default_factory=dict)
    step_id: str | None = None
    dedup_key: str | None = None
    sequence: int = Field(default=1, ge=1)
    depends_on: tuple[str, ...] = Field(default_factory=tuple)

    def to_step(self) -> PlannedStep:
        step_id = self.step_id or self.handler
        return PlannedStep(
            step_id=step_id,
            workflow=self.workflow,
            handler=self.handler,
            params=self.params,
            depends_on=self.depends_on,
            dedup_key=self.dedup_key or f"{self.workflow}:{step_id}",
            sequence=self.sequence,
        )


class SubAgentWorkerResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: WorkerStatus
    handler: str
    step_id: str
    result: StepResult | None = None
    error: str | None = None
    turns: int = 0
    tokens: int = 0
    usd: Decimal = Decimal("0")


class SubAgentWorker:
    def __init__(self, agents: Mapping[str, SubAgent]) -> None:
        self._agents = dict(agents)

    async def dispatch(self, payload: Mapping[str, object] | SubAgentJob) -> SubAgentWorkerResult:
        job = payload if isinstance(payload, SubAgentJob) else SubAgentJob.model_validate(payload)
        step = job.to_step()
        agent = self._agents.get(job.handler)
        if agent is None:
            return SubAgentWorkerResult(
                status="unknown_handler",
                handler=job.handler,
                step_id=step.step_id,
                error=f"No subagent registered for handler {job.handler!r}",
            )
        try:
            result = cast(StepResult, await _maybe_await(agent.run(step)))
        except Exception as exc:
            return SubAgentWorkerResult(
                status="failed",
                handler=job.handler,
                step_id=step.step_id,
                error=_safe_error(exc),
                tokens=_usage_tokens(exc),
                usd=_usage_usd(exc),
            )
        return SubAgentWorkerResult(
            status="succeeded",
            handler=job.handler,
            step_id=step.step_id,
            result=result,
            turns=result.turns,
            tokens=result.tokens,
            usd=result.usd,
        )


def make_worker(agents: Mapping[str, SubAgent]) -> SubAgentWorker:
    return SubAgentWorker(agents)


async def dispatch_subagent_job(
    payload: Mapping[str, object] | SubAgentJob,
    *,
    agents: Mapping[str, SubAgent],
) -> SubAgentWorkerResult:
    return await SubAgentWorker(agents).dispatch(payload)


def _usage_tokens(exc: BaseException) -> int:
    tokens = getattr(exc, "usage_tokens", 0)
    return tokens if isinstance(tokens, int) and tokens >= 0 else 0


def _usage_usd(exc: BaseException) -> Decimal:
    return Decimal(str(getattr(exc, "usage_usd", Decimal("0"))))


def _safe_error(exc: BaseException) -> str:
    message = str(exc)
    lowered = message.lower()
    raw_markers = ("raw_", "raw ", "raw-", "html", "markdown", "document", "content", "page_text")
    if (
        any(marker in lowered for marker in raw_markers)
        or "<body" in lowered
        or "<script" in lowered
        or len(message) > 500
    ):
        return "[redacted]"
    return str(redact_json({"error": message}).get("error", type(exc).__name__))


async def _maybe_await(value: T | Awaitable[T]) -> T:
    if isawaitable(value):
        return await value
    return value
