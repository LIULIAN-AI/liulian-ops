from __future__ import annotations

from collections.abc import Awaitable, Mapping
from inspect import isawaitable
from typing import Any, TypeVar, cast
from uuid import uuid4

from crawler.harness.budget import Budget, BudgetExceededError
from crawler.harness.checkpoint import CheckpointState, JsonValue
from crawler.harness.dispatch import StreamEvent
from crawler.harness.distill import redact_json
from crawler.harness.planner import PlannedStep, WorkflowPlan
from crawler.harness.run_types import (
    AsyncCheckpointStore,
    AsyncEventProducer,
    RunResult,
    StepHandler,
    StepResult,
    StepRunResult,
    budget_result,
    exception_usage,
    failure_result,
    highest_completed_sequence,
)
T = TypeVar("T")


class Orchestrator:
    def __init__(
        self,
        *,
        checkpoint_store: AsyncCheckpointStore,
        budget: Budget,
        step_handlers: Mapping[str, StepHandler],
        producer: AsyncEventProducer | None = None,
    ) -> None:
        self._checkpoint_store = checkpoint_store
        self._budget = budget
        self._step_handlers = step_handlers
        self._producer = producer

    async def run(self, plan: WorkflowPlan) -> RunResult:
        run_id = str(uuid4())
        checkpoint = await self._load_checkpoint(plan.checkpoint_key)
        completed = _completed_steps(checkpoint)
        step_results: list[StepRunResult] = []

        await self._publish_workflow_event("workflow.started", plan, checkpoint, run_id=run_id)
        try:
            self._budget.check()
        except BudgetExceededError as exc:
            await self._publish_workflow_event(
                "workflow.budget_exceeded",
                plan,
                checkpoint,
                run_id=run_id,
                error=str(exc),
            )
            return RunResult(
                workflow=plan.workflow,
                checkpoint_key=plan.checkpoint_key,
                status="budget_exceeded",
                steps=(),
                usage=self._budget.usage,
                error=str(exc),
                checkpoint=checkpoint,
            )

        for step in plan.ordered_steps():
            missing_dependencies = [dependency for dependency in step.depends_on if dependency not in completed]
            if missing_dependencies:
                error = f"Step {step.step_id!r} missing dependencies: {missing_dependencies}"
                result = failure_result(step, error)
                step_results.append(result)
                await self._publish_step_event("step.failed", plan, step, run_id=run_id, result=result)
                await self._publish_workflow_event(
                    "workflow.failed",
                    plan,
                    checkpoint,
                    run_id=run_id,
                    error=error,
                )
                return RunResult(
                    workflow=plan.workflow,
                    checkpoint_key=plan.checkpoint_key,
                    status="failed",
                    steps=tuple(step_results),
                    usage=self._budget.usage,
                    error=error,
                    checkpoint=checkpoint,
                )

            if step.step_id in completed:
                result = StepRunResult(
                    step_id=step.step_id,
                    handler=step.handler,
                    status="skipped",
                    sequence=step.sequence,
                    skipped_reason="completed_checkpoint",
                )
                step_results.append(result)
                await self._publish_step_event("step.skipped", plan, step, run_id=run_id, result=result)
                continue

            try:
                self._budget.check()
            except BudgetExceededError as exc:
                result = budget_result(step, exc)
                step_results.append(result)
                await self._publish_step_event(
                    "step.budget_exceeded",
                    plan,
                    step,
                    run_id=run_id,
                    result=result,
                )
                await self._publish_workflow_event(
                    "workflow.budget_exceeded",
                    plan,
                    checkpoint,
                    run_id=run_id,
                    error=str(exc),
                )
                return RunResult(
                    workflow=plan.workflow,
                    checkpoint_key=plan.checkpoint_key,
                    status="budget_exceeded",
                    steps=tuple(step_results),
                    usage=self._budget.usage,
                    error=str(exc),
                    checkpoint=checkpoint,
                )

            handler = self._step_handlers.get(step.handler)
            if handler is None:
                result = failure_result(step, f"No step handler registered for {step.handler!r}")
                step_results.append(result)
                await self._publish_step_event("step.failed", plan, step, run_id=run_id, result=result)
                await self._publish_workflow_event(
                    "workflow.failed",
                    plan,
                    checkpoint,
                    run_id=run_id,
                    error=result.error,
                )
                return RunResult(
                    workflow=plan.workflow,
                    checkpoint_key=plan.checkpoint_key,
                    status="failed",
                    steps=tuple(step_results),
                    usage=self._budget.usage,
                    error=result.error,
                    checkpoint=checkpoint,
                )

            await self._publish_step_event("step.started", plan, step, run_id=run_id)
            step_result: StepResult | None = None
            try:
                raw_result = cast(
                    StepResult | Mapping[str, Any],
                    await _maybe_await(handler(step)),
                )
                step_result = _coerce_step_result(raw_result)
                checkpoint = await self._save_progress(plan, step, checkpoint, step_result)
                completed.add(step.step_id)
                self._budget.record(
                    tokens=step_result.tokens,
                    usd=step_result.usd,
                    turns=step_result.turns,
                )
            except BudgetExceededError as exc:
                result = budget_result(step, exc, step_result)
                step_results.append(result)
                await self._publish_step_event(
                    "step.budget_exceeded",
                    plan,
                    step,
                    run_id=run_id,
                    result=result,
                )
                await self._publish_workflow_event(
                    "workflow.budget_exceeded",
                    plan,
                    checkpoint,
                    run_id=run_id,
                    error=str(exc),
                )
                return RunResult(
                    workflow=plan.workflow,
                    checkpoint_key=plan.checkpoint_key,
                    status="budget_exceeded",
                    steps=tuple(step_results),
                    usage=self._budget.usage,
                    error=str(exc),
                    checkpoint=checkpoint,
                )
            except Exception as exc:
                try:
                    failed_tokens, failed_usd = exception_usage(exc)
                    if failed_tokens or failed_usd:
                        self._budget.record(tokens=failed_tokens, usd=failed_usd)
                except BudgetExceededError as budget_exc:
                    result = budget_result(
                        step,
                        budget_exc,
                        tokens=failed_tokens,
                        usd=failed_usd,
                    )
                    step_results.append(result)
                    await self._publish_step_event(
                        "step.budget_exceeded",
                        plan,
                        step,
                        run_id=run_id,
                        result=result,
                    )
                    await self._publish_workflow_event(
                        "workflow.budget_exceeded",
                        plan,
                        checkpoint,
                        run_id=run_id,
                        error=str(budget_exc),
                    )
                    return RunResult(
                        workflow=plan.workflow,
                        checkpoint_key=plan.checkpoint_key,
                        status="budget_exceeded",
                        steps=tuple(step_results),
                        usage=self._budget.usage,
                        error=str(budget_exc),
                        checkpoint=checkpoint,
                    )
                result = failure_result(step, str(exc), exc)
                step_results.append(result)
                await self._publish_step_event("step.failed", plan, step, run_id=run_id, result=result)
                await self._publish_workflow_event(
                    "workflow.failed",
                    plan,
                    checkpoint,
                    run_id=run_id,
                    error=str(exc),
                )
                return RunResult(
                    workflow=plan.workflow,
                    checkpoint_key=plan.checkpoint_key,
                    status="failed",
                    steps=tuple(step_results),
                    usage=self._budget.usage,
                    error=str(exc),
                    checkpoint=checkpoint,
                )

            result = StepRunResult(
                step_id=step.step_id,
                handler=step.handler,
                status="succeeded",
                sequence=step.sequence,
                turns=step_result.turns,
                tokens=step_result.tokens,
                usd=step_result.usd,
            )
            step_results.append(result)
            await self._publish_step_event("step.succeeded", plan, step, run_id=run_id, result=result)

        await self._publish_workflow_event("workflow.succeeded", plan, checkpoint, run_id=run_id)
        return RunResult(
            workflow=plan.workflow,
            checkpoint_key=plan.checkpoint_key,
            status="succeeded",
            steps=tuple(step_results),
            usage=self._budget.usage,
            checkpoint=checkpoint,
        )

    async def _load_checkpoint(self, key: str) -> CheckpointState | None:
        state = await _maybe_await(self._checkpoint_store.load(key))
        return state

    async def _save_progress(
        self,
        plan: WorkflowPlan,
        step: PlannedStep,
        checkpoint: CheckpointState | None,
        result: StepResult,
    ) -> CheckpointState:
        existing_metadata = checkpoint.metadata if checkpoint is not None else {}
        metadata = redact_json(existing_metadata)
        completed = _completed_steps_from_metadata(metadata)
        completed[step.step_id] = {
            "sequence": step.sequence,
            "handler": step.handler,
            "metadata": redact_json(result.metadata),
            "state": redact_json(result.state),
        }
        metadata["workflow"] = plan.workflow
        metadata["completed_steps"] = completed
        sequence = max(
            checkpoint.sequence if checkpoint is not None else 0,
            step.sequence,
            highest_completed_sequence(metadata),
        )
        state = CheckpointState(
            key=plan.checkpoint_key,
            cursor=result.cursor if result.cursor is not None else (checkpoint.cursor if checkpoint else None),
            sequence=sequence,
            metadata=metadata,
        )
        saved = await _maybe_await(self._checkpoint_store.save(state))
        return saved

    async def _publish_workflow_event(
        self,
        event_type: str,
        plan: WorkflowPlan,
        checkpoint: CheckpointState | None,
        *,
        run_id: str,
        error: str | None = None,
    ) -> None:
        payload: dict[str, JsonValue] = {
            "workflow": plan.workflow,
            "checkpoint_key": plan.checkpoint_key,
            "run_id": run_id,
            "step_count": len(plan.steps),
        }
        if checkpoint is not None:
            payload["checkpoint_sequence"] = checkpoint.sequence
        if error is not None:
            payload["error"] = error
        await self._publish(
            StreamEvent(
                event_type=event_type,
                payload=payload,
                dedup_key=f"{run_id}:{plan.checkpoint_key}:{event_type}",
            )
        )

    async def _publish_step_event(
        self,
        event_type: str,
        plan: WorkflowPlan,
        step: PlannedStep,
        *,
        run_id: str,
        result: StepRunResult | None = None,
    ) -> None:
        payload: dict[str, JsonValue] = {
            "workflow": plan.workflow,
            "checkpoint_key": plan.checkpoint_key,
            "run_id": run_id,
            "step_id": step.step_id,
            "handler": step.handler,
            "sequence": step.sequence,
            "depends_on": list(step.depends_on),
            "dedup_key": step.dedup_key,
        }
        if result is not None:
            payload["status"] = result.status
            if result.error is not None:
                payload["error"] = result.error
        await self._publish(
            StreamEvent(
                event_type=event_type,
                payload=payload,
                dedup_key=f"{run_id}:{plan.checkpoint_key}:{step.step_id}:{event_type}",
            )
        )

    async def _publish(self, event: StreamEvent) -> None:
        if self._producer is None:
            return
        await _maybe_await(self._producer.publish(event))


def _coerce_step_result(value: StepResult | Mapping[str, Any]) -> StepResult:
    if isinstance(value, StepResult):
        return value
    return StepResult.model_validate(value)


def _completed_steps(checkpoint: CheckpointState | None) -> set[str]:
    if checkpoint is None:
        return set()
    return set(_completed_steps_from_metadata(checkpoint.metadata))


def _completed_steps_from_metadata(metadata: dict[str, JsonValue]) -> dict[str, JsonValue]:
    completed = metadata.get("completed_steps")
    if isinstance(completed, dict):
        return dict(completed)
    return {}


async def _maybe_await(value: T | Awaitable[T]) -> T:
    if isawaitable(value):
        return await value
    return value
