from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal

import pytest

from crawler.extractor.extract import AboutExtraction
from crawler.extractor.router import (
    ExtractionValidationError,
    LLMResultLike,
    LLMRouter,
    OpusEscalation,
)
from crawler.harness.checkpoint import JsonValue


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
    def __init__(self, *results: FakeResult | BaseException) -> None:
        self.results = list(results)
        self.calls: list[dict[str, object]] = []

    async def generate(self, prompt: str, **kwargs: object) -> LLMResultLike:
        self.calls.append({"prompt": prompt, **kwargs})
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


def test_ollama_success_returns_validated_json_and_usage() -> None:
    async def run() -> None:
        ollama = FakeLLM(
            FakeResult(
                model="llama3.1",
                text="",
                json_value={
                    "company_name": "Neo Bank",
                    "description": "Digital bank",
                    "confidence": 0.91,
                },
                usage=FakeUsage(total_tokens=17, cost_usd=Decimal("0")),
            )
        )
        anthropic = FakeLLM()

        routed = await LLMRouter(ollama=ollama, anthropic=anthropic).extract(
            prompt="extract",
            system="system",
            schema=AboutExtraction,
        )

        assert routed.tier == "ollama"
        assert routed.model == "llama3.1"
        assert routed.json_value["company_name"] == "Neo Bank"
        assert routed.confidence == 0.91
        assert routed.usage_tokens == 17
        assert anthropic.calls == []
        assert ollama.calls[0]["temperature"] == 0

    asyncio.run(run())


def test_ollama_invalid_json_falls_back_to_anthropic_sonnet() -> None:
    async def run() -> None:
        ollama = FakeLLM(FakeResult(model="llama3.1", text="not-json"))
        anthropic = FakeLLM(
            FakeResult(
                model="claude-sonnet",
                text="",
                json_value={"company_name": "Neo Bank", "confidence": 0.87},
                usage=FakeUsage(total_tokens=20, cost_usd=Decimal("0.0003")),
            )
        )

        routed = await LLMRouter(ollama=ollama, anthropic=anthropic).extract(
            prompt="extract",
            system="system",
            schema=AboutExtraction,
        )

        assert routed.tier == "anthropic_sonnet"
        assert [attempt.tier for attempt in routed.attempts] == ["ollama", "anthropic_sonnet"]
        assert routed.attempts[0].valid is False
        assert routed.usage_tokens == 20
        assert routed.usage_usd == Decimal("0.0003")

    asyncio.run(run())


def test_low_confidence_ollama_falls_back_to_anthropic() -> None:
    async def run() -> None:
        ollama = FakeLLM(
            FakeResult(model="llama3.1", text='{"company_name":"Neo","confidence":0.2}')
        )
        anthropic = FakeLLM(
            FakeResult(model="claude-sonnet", text='{"company_name":"Neo","confidence":0.8}')
        )

        routed = await LLMRouter(ollama=ollama, anthropic=anthropic).extract(
            prompt="extract",
            system="system",
            schema=AboutExtraction,
        )

        assert routed.tier == "anthropic_sonnet"
        assert routed.attempts[0].confidence == 0.2
        assert "below threshold" in str(routed.attempts[0].error)

    asyncio.run(run())


def test_repeated_validation_failure_raises_without_opus() -> None:
    async def run() -> None:
        ollama = FakeLLM(FakeResult(model="llama3.1", text='{"confidence":2}'))
        anthropic = FakeLLM(FakeResult(model="claude-sonnet", text='{"confidence":2}'))

        with pytest.raises(ExtractionValidationError, match="anthropic_sonnet"):
            await LLMRouter(ollama=ollama, anthropic=anthropic).extract(
                prompt="extract",
                system="system",
                schema=AboutExtraction,
            )

    asyncio.run(run())


def test_repeated_validation_failure_can_escalate_to_opus() -> None:
    async def run() -> None:
        ollama = FakeLLM(FakeResult(model="llama3.1", text='{"confidence":2}'))
        anthropic = FakeLLM(
            FakeResult(model="claude-sonnet", text='{"confidence":2}'),
            FakeResult(model="claude-opus", text='{"company_name":"Neo","confidence":0.92}'),
        )

        routed = await LLMRouter(
            ollama=ollama,
            anthropic=anthropic,
            opus=OpusEscalation(enabled=True, model="claude-opus", max_tokens=2048),
        ).extract(prompt="extract", system="system", schema=AboutExtraction)

        assert routed.tier == "anthropic_opus"
        assert [attempt.tier for attempt in routed.attempts] == [
            "ollama",
            "anthropic_sonnet",
            "anthropic_opus",
        ]
        assert anthropic.calls[1]["model"] == "claude-opus"
        assert anthropic.calls[1]["max_tokens"] == 2048

    asyncio.run(run())


def test_fake_clients_prevent_network_calls() -> None:
    async def run() -> None:
        ollama = FakeLLM(FakeResult(model="local", text='{"confidence":0.9}'))

        await LLMRouter(ollama=ollama, anthropic=None).extract(
            prompt="offline",
            system="system",
            schema=AboutExtraction,
        )

        assert len(ollama.calls) == 1

    asyncio.run(run())
