from __future__ import annotations

import asyncio
from decimal import Decimal

from crawler.harness.budget import Budget, BudgetLimits
from crawler.harness.checkpoint import CheckpointState, InMemoryCheckpointStore
from crawler.harness.dispatch import InMemoryStreamProducer, PublishResult, StreamEvent
from crawler.harness.orchestrator import Orchestrator
from crawler.harness.planner import PlannedStep, WorkflowPlan, plan_workflow
from crawler.harness.run_types import StepResult


def test_orchestrator_runs_steps_in_dependency_order_and_records_usage() -> None:
    async def run() -> None:
        calls: list[str] = []

        async def handler(step: object) -> StepResult:
            step_id = getattr(step, "step_id")
            calls.append(step_id)
            return StepResult(turns=1, tokens=10, usd=Decimal("0.05"))

        plan = plan_workflow("refresh_field_group", bank_id="bank-1", field_group="product")
        result = await Orchestrator(
            checkpoint_store=InMemoryCheckpointStore(),
            budget=Budget(BudgetLimits(tokens=100, usd=Decimal("1"), turns=5)),
            step_handlers={"refresh_company": handler, "refresh_field_group": handler},
        ).run(plan)

        assert result.status == "succeeded"
        assert calls == [step.step_id for step in plan.steps]
        assert result.usage.tokens == 20
        assert result.usage.turns == 2
        assert result.usage.usd == Decimal("0.10")
        assert [step.status for step in result.steps] == ["succeeded", "succeeded"]

    asyncio.run(run())


def test_orchestrator_stops_on_failure_and_does_not_run_dependents() -> None:
    async def run() -> None:
        calls: list[str] = []

        async def fail(step: object) -> StepResult:
            calls.append(getattr(step, "handler"))
            raise RuntimeError("boom")

        async def should_not_run(step: object) -> StepResult:
            calls.append(getattr(step, "handler"))
            return StepResult()

        plan = plan_workflow("refresh_field_group", bank_id="bank-1", field_group="staff")
        result = await Orchestrator(
            checkpoint_store=InMemoryCheckpointStore(),
            budget=Budget(BudgetLimits()),
            step_handlers={"refresh_company": fail, "refresh_field_group": should_not_run},
        ).run(plan)

        assert result.status == "failed"
        assert calls == ["refresh_company"]
        assert len(result.steps) == 1
        assert result.steps[0].status == "failed"
        assert result.error == "boom"

    asyncio.run(run())


def test_orchestrator_enforces_dependencies_even_when_plan_is_reordered() -> None:
    async def run() -> None:
        async def handler(step: PlannedStep) -> StepResult:
            return StepResult()

        ordered = plan_workflow("refresh_field_group", bank_id="bank-1", field_group="staff")
        reordered = WorkflowPlan(
            workflow=ordered.workflow,
            checkpoint_key=ordered.checkpoint_key,
            params=ordered.params,
            steps=(ordered.steps[1], ordered.steps[0]),
        )

        result = await Orchestrator(
            checkpoint_store=InMemoryCheckpointStore(),
            budget=Budget(BudgetLimits()),
            step_handlers={"refresh_company": handler, "refresh_field_group": handler},
        ).run(reordered)

        assert result.status == "failed"
        assert "missing dependencies" in str(result.error)

    asyncio.run(run())


def test_orchestrator_resumes_from_checkpoint_and_skips_completed_steps() -> None:
    async def run() -> None:
        store = InMemoryCheckpointStore()
        plan = plan_workflow("refresh_field_group", bank_id="bank-1", field_group="financials")
        store.save(
            CheckpointState(
                key=plan.checkpoint_key,
                sequence=1,
                metadata={"completed_steps": {plan.steps[0].step_id: {"sequence": 1}}},
            )
        )
        calls: list[str] = []

        async def handler(step: object) -> StepResult:
            calls.append(getattr(step, "handler"))
            return StepResult(tokens=3)

        result = await Orchestrator(
            checkpoint_store=store,
            budget=Budget(BudgetLimits(tokens=10)),
            step_handlers={"refresh_company": handler, "refresh_field_group": handler},
        ).run(plan)

        assert result.status == "succeeded"
        assert calls == ["refresh_field_group"]
        assert [step.status for step in result.steps] == ["skipped", "succeeded"]
        saved = store.load(plan.checkpoint_key)
        assert saved is not None
        assert saved.sequence == 2

    asyncio.run(run())


def test_orchestrator_keeps_sequence_at_highest_completed_step_when_saving() -> None:
    async def run() -> None:
        store = InMemoryCheckpointStore()
        plan = plan_workflow("discover_banks")
        store.save(
            CheckpointState(
                key=plan.checkpoint_key,
                sequence=1,
                metadata={"completed_steps": {"future-step": {"sequence": 5}}},
            )
        )

        async def handler(step: object) -> StepResult:
            return StepResult()

        await Orchestrator(
            checkpoint_store=store,
            budget=Budget(BudgetLimits()),
            step_handlers={"discover_banks": handler},
        ).run(plan)

        saved = store.load(plan.checkpoint_key)
        assert saved is not None
        assert saved.sequence == 5

    asyncio.run(run())


def test_orchestrator_reports_budget_exceeded_after_step_accounting() -> None:
    async def run() -> None:
        calls = 0

        async def handler(step: object) -> StepResult:
            nonlocal calls
            calls += 1
            return StepResult(tokens=11)

        plan = plan_workflow("discover_banks")
        result = await Orchestrator(
            checkpoint_store=InMemoryCheckpointStore(),
            budget=Budget(BudgetLimits(tokens=10)),
            step_handlers={"discover_banks": handler},
        ).run(plan)

        assert calls == 1
        assert result.status == "budget_exceeded"
        assert result.steps[0].status == "budget_exceeded"
        assert result.steps[0].tokens == 11
        assert result.usage.tokens == 11
        saved = result.checkpoint
        assert saved is not None
        completed_steps = saved.metadata["completed_steps"]
        assert isinstance(completed_steps, dict)
        assert plan.steps[0].step_id in completed_steps

    asyncio.run(run())


def test_orchestrator_emits_dispatch_events() -> None:
    async def run() -> None:
        async def handler(step: object) -> StepResult:
            return StepResult(tokens=1)

        producer = InMemoryStreamProducer(stream="test")
        plan = plan_workflow("discover_banks")
        result = await Orchestrator(
            checkpoint_store=InMemoryCheckpointStore(),
            budget=Budget(BudgetLimits(tokens=10)),
            step_handlers={"discover_banks": handler},
            producer=producer,
        ).run(plan)

        assert result.status == "succeeded"
        assert [event.event_type for event in producer.events] == [
            "workflow.started",
            "step.started",
            "step.succeeded",
            "workflow.succeeded",
        ]
        assert producer.events[1].payload["step_id"] == plan.steps[0].step_id

    asyncio.run(run())


def test_orchestrator_event_dedup_keys_are_attempt_scoped() -> None:
    async def run() -> None:
        def handler(step: object) -> StepResult:
            return StepResult(tokens=1)

        producer = InMemoryStreamProducer(stream="test")
        store = InMemoryCheckpointStore()
        plan = plan_workflow("discover_banks")

        first = await Orchestrator(
            checkpoint_store=store,
            budget=Budget(BudgetLimits(tokens=10)),
            step_handlers={"discover_banks": handler},
            producer=producer,
        ).run(plan)
        second = await Orchestrator(
            checkpoint_store=store,
            budget=Budget(BudgetLimits(tokens=10)),
            step_handlers={"discover_banks": handler},
            producer=producer,
        ).run(plan)

        assert first.status == "succeeded"
        assert second.status == "succeeded"
        assert [event.event_type for event in producer.events].count("workflow.started") == 2
        run_ids = {event.payload["run_id"] for event in producer.events}
        assert len(run_ids) == 2

    asyncio.run(run())


def test_orchestrator_redacts_raw_content_from_checkpoint_metadata() -> None:
    async def run() -> None:
        async def handler(step: object) -> StepResult:
            return StepResult(
                metadata={
                    "raw_html": "<html>secret</html>",
                    "nested": {"document": "secret pdf text", "safe": "ok"},
                    "items": [{"body_html": "<p>secret</p>"}],
                    "html_content": "<html>secret</html>",
                    "bodyText": "secret body",
                    "markdown": "# secret",
                },
                state={"rawContent": "secret", "page_text": "secret", "safe": True},
            )

        store = InMemoryCheckpointStore()
        plan = plan_workflow("discover_banks")
        await Orchestrator(
            checkpoint_store=store,
            budget=Budget(BudgetLimits()),
            step_handlers={"discover_banks": handler},
        ).run(plan)

        saved = store.load(plan.checkpoint_key)
        assert saved is not None
        completed = saved.metadata["completed_steps"]
        assert isinstance(completed, dict)
        saved_step = completed[plan.steps[0].step_id]
        assert isinstance(saved_step, dict)
        metadata = saved_step["metadata"]
        state = saved_step["state"]
        assert isinstance(metadata, dict)
        assert isinstance(state, dict)
        assert metadata["raw_html"] == "[redacted]"
        assert metadata["nested"] == {"document": "[redacted]", "safe": "ok"}
        assert metadata["items"] == [{"body_html": "[redacted]"}]
        assert metadata["html_content"] == "[redacted]"
        assert metadata["bodyText"] == "[redacted]"
        assert metadata["markdown"] == "[redacted]"
        assert state["rawContent"] == "[redacted]"
        assert state["page_text"] == "[redacted]"
        assert state["safe"] is True

    asyncio.run(run())


def test_orchestrator_uses_async_checkpoint_store_and_async_producer_adapter() -> None:
    class AsyncStore:
        def __init__(self) -> None:
            self.inner = InMemoryCheckpointStore()

        async def load(self, key: str) -> CheckpointState | None:
            return self.inner.load(key)

        async def save(self, state: CheckpointState) -> CheckpointState:
            return self.inner.save(state)

    class AsyncProducer:
        def __init__(self) -> None:
            self.events: list[str] = []

        async def publish(self, event: StreamEvent) -> PublishResult:
            self.events.append(event.event_type)
            return PublishResult(stream="test", stream_id="async-1")

    async def run() -> None:
        async def handler(step: object) -> dict[str, int]:
            return {"tokens": 1}

        producer = AsyncProducer()
        result = await Orchestrator(
            checkpoint_store=AsyncStore(),
            budget=Budget(BudgetLimits(tokens=5)),
            step_handlers={"discover_banks": handler},
            producer=producer,
        ).run(plan_workflow("discover_banks"))

        assert result.status == "succeeded"
        assert producer.events[-1] == "workflow.succeeded"

    asyncio.run(run())
