from __future__ import annotations

import asyncio
from decimal import Decimal
import warnings

import pytest

from crawler.agents.base import (
    AgentBudgetError,
    AgentConfig,
    AgentInputEnvelope,
    AgentOutputEnvelope,
    AgentToolError,
    SubAgent,
    ToolContext,
    ToolRegistry,
    make_dedup_key,
)
from crawler.harness.budget import Budget, BudgetLimits
from crawler.harness.checkpoint import CheckpointState, JsonValue
from crawler.harness.orchestrator import Orchestrator
from crawler.harness.planner import PlannedStep, WorkflowPlan, plan_workflow


class EchoAgent(SubAgent):
    config = AgentConfig(name="echo", role="unit test agent", tool_names=("echo",))

    async def execute(
        self,
        envelope: AgentInputEnvelope,
        context: ToolContext,
    ) -> AgentOutputEnvelope:
        value = await context.call_tool("echo", envelope.params["value"])
        return AgentOutputEnvelope(
            metadata={"value": str(value), "temperature": envelope.temperature},
            state={"dedup_key": envelope.dedup_key},
            turns=1,
            tokens=2,
            usd=Decimal("0.01"),
        )


def test_subclass_execution_returns_step_result_and_records_budget() -> None:
    async def run() -> None:
        registry = ToolRegistry()
        registry.register("echo", lambda value: f"seen:{value}")
        budget = Budget(BudgetLimits(tokens=10, turns=2, usd=Decimal("0.10")))
        step = _step(params={"value": "bank-1"})

        result = await EchoAgent(registry=registry, budget=budget).run(step)

        assert result.metadata == {"value": "seen:bank-1", "temperature": 0.0}
        assert result.state == {"dedup_key": step.dedup_key}
        assert result.tokens == 2
        assert budget.usage.tokens == 0
        assert budget.usage.turns == 0

    asyncio.run(run())


def test_budget_is_checked_before_execution() -> None:
    class FakeClock:
        def __init__(self) -> None:
            self.now = 10.0

        def __call__(self) -> float:
            return self.now

    class ShouldNotRun(SubAgent):
        config = AgentConfig(name="budgeted", role="unit")

        async def execute(
            self,
            envelope: AgentInputEnvelope,
            context: ToolContext,
        ) -> AgentOutputEnvelope:
            raise AssertionError("execute should not run")

    async def run() -> None:
        clock = FakeClock()
        budget = Budget(BudgetLimits(wall_seconds=1), clock=clock)
        clock.now = 12.0

        with pytest.raises(AgentBudgetError) as exc:
            await ShouldNotRun(budget=budget).run(_step())

        assert exc.value.dimension == "wall_seconds"

    asyncio.run(run())


def test_budget_is_checked_after_execution() -> None:
    class ExpensiveAgent(SubAgent):
        config = AgentConfig(name="expensive", role="unit")

        async def execute(
            self,
            envelope: AgentInputEnvelope,
            context: ToolContext,
        ) -> AgentOutputEnvelope:
            return AgentOutputEnvelope(tokens=3)

    async def run() -> None:
        result = await ExpensiveAgent(budget=Budget(BudgetLimits(tokens=2))).run(_step())

        assert result.tokens == 3

    asyncio.run(run())


def test_agent_does_not_double_count_shared_orchestrator_budget() -> None:
    class TokenAgent(SubAgent):
        config = AgentConfig(name="token", role="unit")

        async def execute(
            self,
            envelope: AgentInputEnvelope,
            context: ToolContext,
        ) -> AgentOutputEnvelope:
            return AgentOutputEnvelope(tokens=6)

    async def run() -> None:
        budget = Budget(BudgetLimits(tokens=10))
        agent = TokenAgent(budget=budget)
        result = await Orchestrator(
            checkpoint_store=_MemoryStore(),
            budget=budget,
            step_handlers={"unit": agent.run},
        ).run(_workflow(_step()))

        assert result.status == "succeeded"
        assert result.usage.tokens == 6

    asyncio.run(run())


def test_agent_budget_error_is_reported_as_orchestrator_budget_exceeded() -> None:
    class FakeClock:
        def __init__(self) -> None:
            self.now = 10.0

        def __call__(self) -> float:
            return self.now

    class TimeAgent(SubAgent):
        config = AgentConfig(name="time", role="unit")

        async def execute(
            self,
            envelope: AgentInputEnvelope,
            context: ToolContext,
        ) -> AgentOutputEnvelope:
            clock.now = 12.0
            return AgentOutputEnvelope()

    async def run() -> None:
        budget = Budget(BudgetLimits(wall_seconds=1), clock=clock)
        agent = TimeAgent(budget=budget)
        result = await Orchestrator(
            checkpoint_store=_MemoryStore(),
            budget=budget,
            step_handlers={"unit": agent.run},
        ).run(_workflow(_step()))

        assert result.status == "budget_exceeded"
        assert result.steps[0].status == "budget_exceeded"

    clock = FakeClock()
    asyncio.run(run())


def test_tool_allow_list_rejects_undeclared_tool() -> None:
    class ToolAgent(SubAgent):
        config = AgentConfig(name="tools", role="unit", tool_names=("allowed",))

        async def execute(
            self,
            envelope: AgentInputEnvelope,
            context: ToolContext,
        ) -> AgentOutputEnvelope:
            await context.call_tool("blocked")
            return AgentOutputEnvelope()

    async def run() -> None:
        registry = ToolRegistry()
        registry.register("blocked", lambda: "nope")

        with pytest.raises(AgentToolError, match="not declared"):
            await ToolAgent(registry=registry).run(_step())

    asyncio.run(run())


def test_tool_context_does_not_expose_plain_registry_attribute() -> None:
    context = ToolContext(registry=ToolRegistry(), allowed_tool_names=())

    assert not hasattr(context, "_registry")


def test_redacts_raw_content_before_returning_step_result() -> None:
    class RawAgent(SubAgent):
        config = AgentConfig(name="raw", role="unit")

        async def execute(
            self,
            envelope: AgentInputEnvelope,
            context: ToolContext,
        ) -> AgentOutputEnvelope:
            return AgentOutputEnvelope(
                metadata={
                    "raw_html": "<html>secret</html>",
                    "nested": {"document": "secret", "safe": "ok"},
                },
                state={"page_text": "secret", "safe": True},
            )

    async def run() -> None:
        result = await RawAgent().run(_step())

        assert result.metadata == {
            "raw_html": "[redacted]",
            "nested": {"document": "[redacted]", "safe": "ok"},
        }
        assert result.state == {"page_text": "[redacted]", "safe": True}

    asyncio.run(run())


def test_dedup_key_helper_matches_planner_deterministically() -> None:
    first = make_dedup_key(
        "refresh_field_group",
        "refresh_field_group",
        {"sort_id": 7, "field_group": "marketing", "bank_id": "bank-1"},
    )
    second = make_dedup_key(
        "refresh_field_group",
        "refresh_field_group",
        {"bank_id": "bank-1", "field_group": "marketing", "sort_id": 7},
    )
    plan = plan_workflow(
        "refresh_field_group",
        bank_id="bank-1",
        field_group="marketing",
        sort_id=7,
    )

    assert first == second
    assert first == plan.steps[1].dedup_key
    assert make_dedup_key("discover_banks", "discover_banks") == "discover_banks:discover_banks"


def test_default_temperature_is_zero_unless_config_overrides() -> None:
    class DefaultAgent(SubAgent):
        config = AgentConfig(name="default-temp", role="unit")

        async def execute(
            self,
            envelope: AgentInputEnvelope,
            context: ToolContext,
        ) -> AgentOutputEnvelope:
            return AgentOutputEnvelope(metadata={"temperature": envelope.temperature})

    class WarmAgent(SubAgent):
        config = AgentConfig(name="warm-temp", role="unit", temperature=0.4)

        async def execute(
            self,
            envelope: AgentInputEnvelope,
            context: ToolContext,
        ) -> AgentOutputEnvelope:
            return AgentOutputEnvelope(metadata={"temperature": envelope.temperature})

    async def run() -> None:
        default = await DefaultAgent().run(_step())
        warm = await WarmAgent().run(_step())

        assert default.metadata["temperature"] == 0.0
        assert warm.metadata["temperature"] == 0.4

    asyncio.run(run())


def test_mysql_write_tool_rejected_by_default() -> None:
    class MySqlAgent(SubAgent):
        config = AgentConfig(name="mysql", role="unit", tool_names=("mysql_write",))

        async def execute(
            self,
            envelope: AgentInputEnvelope,
            context: ToolContext,
        ) -> AgentOutputEnvelope:
            await context.call_tool("mysql_write")
            return AgentOutputEnvelope()

    async def run() -> None:
        registry = ToolRegistry()
        registry.register("mysql_write", lambda: "wrote", writes_mysql=True)

        with pytest.raises(AgentToolError, match="MySQL"):
            await MySqlAgent(registry=registry).run(_step())

    asyncio.run(run())


def test_tool_context_supports_sync_and_async_tool_calls() -> None:
    async def async_echo(value: str) -> str:
        return f"async:{value}"

    class MixedToolAgent(SubAgent):
        config = AgentConfig(name="mixed", role="unit", tool_names=("sync", "async"))

        async def execute(
            self,
            envelope: AgentInputEnvelope,
            context: ToolContext,
        ) -> AgentOutputEnvelope:
            sync_value = context.call_tool_sync("sync", "one")
            async_value = await context.call_tool("async", "two")
            return AgentOutputEnvelope(metadata={"sync": str(sync_value), "async": str(async_value)})

    async def run() -> None:
        registry = ToolRegistry()
        registry.register("sync", lambda value: f"sync:{value}")
        registry.register("async", async_echo)

        result = await MixedToolAgent(registry=registry).run(_step())

        assert result.metadata == {"sync": "sync:one", "async": "async:two"}

    asyncio.run(run())


def test_sync_tool_call_closes_rejected_coroutine() -> None:
    async def async_echo() -> str:
        return "async"

    registry = ToolRegistry()
    registry.register("async", async_echo)
    context = ToolContext(registry=registry, allowed_tool_names=("async",))

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with pytest.raises(AgentToolError, match="use call_tool"):
            context.call_tool_sync("async")

    assert [warning for warning in caught if "was never awaited" in str(warning.message)] == []


def test_dedup_key_helper_drops_none_params_like_planner() -> None:
    plan = plan_workflow("discover_banks", country="US", unused=None)

    assert make_dedup_key("discover_banks", "discover_banks", {"country": "US", "unused": None}) == (
        plan.steps[0].dedup_key
    )


def test_output_contract_rejects_unknown_fields() -> None:
    class BadOutputAgent(SubAgent):
        config = AgentConfig(name="bad-output", role="unit")

        async def execute(
            self,
            envelope: AgentInputEnvelope,
            context: ToolContext,
        ) -> dict[str, object]:
            return {"toknes": 1}

    async def run() -> None:
        with pytest.raises(Exception, match="invalid output envelope"):
            await BadOutputAgent().run(_step())

    asyncio.run(run())


def _step(params: dict[str, JsonValue] | None = None) -> PlannedStep:
    return PlannedStep(
        step_id="unit",
        workflow="discover_banks",
        handler="unit",
        params=params or {},
        dedup_key="discover_banks:unit",
        sequence=1,
    )


def _workflow(step: PlannedStep) -> WorkflowPlan:
    return WorkflowPlan(
        workflow=step.workflow,
        checkpoint_key="agent-test",
        params={},
        steps=(step,),
    )


class _MemoryStore:
    def __init__(self) -> None:
        self.state: CheckpointState | None = None

    async def load(self, key: str) -> CheckpointState | None:
        return self.state

    async def save(self, state: CheckpointState) -> CheckpointState:
        self.state = state
        return state
