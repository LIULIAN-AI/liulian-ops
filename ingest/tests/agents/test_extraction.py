from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal

import pytest

from crawler.agents.base import ToolRegistry
from crawler.agents.extraction import ExtractionAgent
from crawler.extractor.router import ExtractionValidationError, LLMResultLike, LLMRouter
from crawler.harness.budget import Budget, BudgetLimits
from crawler.harness.checkpoint import CheckpointState, JsonValue
from crawler.harness.orchestrator import Orchestrator
from crawler.harness.planner import PlannedStep
from crawler.tools.http import RawDoc


@dataclass(frozen=True)
class FakeUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost_usd: Decimal = Decimal("0")


@dataclass(frozen=True)
class FakeResult:
    model: str
    text: str
    json_value: JsonValue = None
    usage: FakeUsage = FakeUsage()


class FakeLLM:
    def __init__(self, result: FakeResult) -> None:
        self.result = result
        self.prompts: list[str] = []

    async def generate(self, prompt: str, **kwargs: object) -> LLMResultLike:
        _ = kwargs
        self.prompts.append(prompt)
        return self.result


def test_extraction_agent_accepts_raw_doc_params_and_redacts_raw_content() -> None:
    async def run() -> None:
        llm = FakeLLM(
            FakeResult(
                model="llama3.1",
                text="",
                json_value={
                    "company_name": "Neo Bank",
                    "description": "Digital banking",
                    "confidence": 0.93,
                },
                usage=FakeUsage(total_tokens=33, cost_usd=Decimal("0.0004")),
            )
        )
        raw_doc = RawDoc(
            source_url="https://example.test/about",
            content_type="text/html",
            text="<html>secret raw page text about Neo Bank</html>",
        )

        result = await ExtractionAgent(router=LLMRouter(ollama=llm, anthropic=None)).run(
            _step(
                {
                    "field_group": "about",
                    "raw_doc": raw_doc.model_dump(mode="json"),
                    "max_content_chars": 1000,
                }
            )
        )

        assert result.state["extracted"] == {
            "company_name": "Neo Bank",
            "description": "Digital banking",
            "headquarters": None,
            "founded_year": None,
            "confidence": 0.93,
        }
        assert result.metadata["model"] == "llama3.1"
        assert result.metadata["tier"] == "ollama"
        assert result.metadata["source_url"] == "https://example.test/about"
        assert result.metadata["content_hash"] == raw_doc.content_hash
        assert result.tokens == 33
        assert result.usd == Decimal("0.0004")
        assert "secret raw page text" in llm.prompts[0]
        assert "secret raw page text" not in str(result.metadata)
        assert "secret raw page text" not in str(result.state)

    asyncio.run(run())


def test_extraction_agent_can_read_raw_doc_from_declared_context_tool() -> None:
    async def run() -> None:
        llm = FakeLLM(
            FakeResult(model="local", text='{"company_name":"Neo","confidence":0.9}')
        )

        async def raw_doc_tool(**kwargs: object) -> RawDoc:
            assert kwargs == {"snapshot_id": "snap-1"}
            return RawDoc(source_url="https://example.test", text="Neo is a bank.")

        registry = ToolRegistry()
        registry.register("extract.raw_doc", raw_doc_tool)

        result = await ExtractionAgent(
            router=LLMRouter(ollama=llm, anthropic=None),
            registry=registry,
        ).run(_step({"field_group": "about", "snapshot_id": "snap-1"}))

        provenance = result.state["provenance"]
        extracted = result.state["extracted"]
        assert isinstance(provenance, dict)
        assert isinstance(extracted, dict)
        assert provenance["source_url"] == "https://example.test"
        assert extracted["company_name"] == "Neo"

    asyncio.run(run())


def test_extraction_agent_usage_accounts_fallback_attempts() -> None:
    async def run() -> None:
        class SequenceLLM:
            def __init__(self, *results: FakeResult) -> None:
                self.results = list(results)

            async def generate(self, prompt: str, **kwargs: object) -> LLMResultLike:
                _ = prompt, kwargs
                return self.results.pop(0)

        ollama = SequenceLLM(
            FakeResult(
                model="llama3.1",
                text='{"confidence":0.1}',
                usage=FakeUsage(total_tokens=5, cost_usd=Decimal("0")),
            )
        )
        anthropic = SequenceLLM(
            FakeResult(
                model="claude-sonnet",
                text='{"company_name":"Neo","confidence":0.9}',
                usage=FakeUsage(total_tokens=11, cost_usd=Decimal("0.002")),
            )
        )
        raw_doc = RawDoc(source_url="https://example.test", text="Neo is a bank.")

        result = await ExtractionAgent(router=LLMRouter(ollama=ollama, anthropic=anthropic)).run(
            _step({"field_group": "about", "raw_doc": raw_doc.model_dump(mode="json")})
        )

        assert result.tokens == 16
        assert result.usd == Decimal("0.002")
        assert result.turns == 2
        assert result.metadata["usage"] == {"tokens": 16, "usd": "0.002"}

    asyncio.run(run())


def test_extraction_agent_rejects_raw_source_copied_into_valid_field() -> None:
    async def run() -> None:
        raw_doc = RawDoc(
            source_url="https://example.test",
            text="<html><body>" + ("secret raw page text " * 30) + "</body></html>",
        )
        llm = FakeLLM(
            FakeResult(
                model="local",
                text="",
                json_value={
                    "company_name": "Neo",
                    "description": raw_doc.text,
                    "confidence": 0.9,
                },
            )
        )

        with pytest.raises(ExtractionValidationError, match="raw source content"):
            await ExtractionAgent(router=LLMRouter(ollama=llm, anthropic=None)).run(
                _step({"field_group": "about", "raw_doc": raw_doc.model_dump(mode="json")})
            )

    asyncio.run(run())


def test_failed_extraction_attempts_are_accounted_by_orchestrator() -> None:
    class SequenceLLM:
        def __init__(self, *results: FakeResult) -> None:
            self.results = list(results)

        async def generate(self, prompt: str, **kwargs: object) -> LLMResultLike:
            _ = prompt, kwargs
            return self.results.pop(0)

    class MemoryStore:
        async def load(self, key: str) -> CheckpointState | None:
            _ = key
            return None

        async def save(self, state: CheckpointState) -> CheckpointState:
            return state

    async def run() -> None:
        ollama = SequenceLLM(
            FakeResult(model="llama3.1", text='{"confidence":2}', usage=FakeUsage(total_tokens=5))
        )
        anthropic = SequenceLLM(
            FakeResult(
                model="claude-sonnet",
                text='{"confidence":2}',
                usage=FakeUsage(total_tokens=11, cost_usd=Decimal("0.002")),
            )
        )
        agent = ExtractionAgent(router=LLMRouter(ollama=ollama, anthropic=anthropic))
        raw_doc = RawDoc(source_url="https://example.test", text="Neo is a bank.")
        step = _step({"field_group": "about", "raw_doc": raw_doc.model_dump(mode="json")})
        from crawler.harness.planner import WorkflowPlan

        result = await Orchestrator(
            checkpoint_store=MemoryStore(),
            budget=Budget(BudgetLimits(tokens=100, usd=Decimal("1"))),
            step_handlers={"refresh_field_group": agent.run},
        ).run(
            WorkflowPlan(
                workflow="refresh_field_group",
                checkpoint_key="extract-fail",
                params={},
                steps=(step,),
            )
        )

        assert result.status == "failed"
        assert result.steps[0].tokens == 16
        assert result.steps[0].usd == Decimal("0.002")
        assert result.usage.tokens == 16
        assert result.usage.usd == Decimal("0.002")

    asyncio.run(run())


def test_raw_leak_rejection_is_accounted_by_orchestrator() -> None:
    class MemoryStore:
        async def load(self, key: str) -> CheckpointState | None:
            _ = key
            return None

        async def save(self, state: CheckpointState) -> CheckpointState:
            return state

    async def run() -> None:
        raw_doc = RawDoc(
            source_url="https://example.test",
            text="<html><body>" + ("secret raw page text " * 30) + "</body></html>",
        )
        llm = FakeLLM(
            FakeResult(
                model="local",
                text="",
                json_value={
                    "company_name": "Neo",
                    "description": raw_doc.text,
                    "confidence": 0.9,
                },
                usage=FakeUsage(total_tokens=12, cost_usd=Decimal("0.003")),
            )
        )
        agent = ExtractionAgent(router=LLMRouter(ollama=llm, anthropic=None))
        step = _step({"field_group": "about", "raw_doc": raw_doc.model_dump(mode="json")})
        from crawler.harness.planner import WorkflowPlan

        result = await Orchestrator(
            checkpoint_store=MemoryStore(),
            budget=Budget(BudgetLimits(tokens=100, usd=Decimal("1"))),
            step_handlers={"refresh_field_group": agent.run},
        ).run(
            WorkflowPlan(
                workflow="refresh_field_group",
                checkpoint_key="extract-raw-leak",
                params={},
                steps=(step,),
            )
        )

        assert result.status == "failed"
        assert result.steps[0].tokens == 12
        assert result.steps[0].usd == Decimal("0.003")
        assert result.usage.tokens == 12
        assert result.usage.usd == Decimal("0.003")

    asyncio.run(run())


def test_failed_extraction_usage_is_visible_when_it_exceeds_budget() -> None:
    class MemoryStore:
        async def load(self, key: str) -> CheckpointState | None:
            _ = key
            return None

        async def save(self, state: CheckpointState) -> CheckpointState:
            return state

    async def run() -> None:
        llm = FakeLLM(
            FakeResult(
                model="local",
                text='{"confidence":2}',
                usage=FakeUsage(total_tokens=12, cost_usd=Decimal("0.003")),
            )
        )
        raw_doc = RawDoc(source_url="https://example.test", text="Neo is a bank.")
        agent = ExtractionAgent(router=LLMRouter(ollama=llm, anthropic=None))
        step = _step({"field_group": "about", "raw_doc": raw_doc.model_dump(mode="json")})
        from crawler.harness.planner import WorkflowPlan

        result = await Orchestrator(
            checkpoint_store=MemoryStore(),
            budget=Budget(BudgetLimits(tokens=10)),
            step_handlers={"refresh_field_group": agent.run},
        ).run(
            WorkflowPlan(
                workflow="refresh_field_group",
                checkpoint_key="extract-budget-fail",
                params={},
                steps=(step,),
            )
        )

        assert result.status == "budget_exceeded"
        assert result.steps[0].status == "budget_exceeded"
        assert result.steps[0].tokens == 12
        assert result.usage.tokens == 12

    asyncio.run(run())


def _step(params: dict[str, JsonValue]) -> PlannedStep:
    return PlannedStep(
        step_id="extract",
        workflow="refresh_field_group",
        handler="refresh_field_group",
        params=params,
        dedup_key="refresh_field_group:extract",
        sequence=1,
    )
