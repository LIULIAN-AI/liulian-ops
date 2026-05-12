from __future__ import annotations

import asyncio
from decimal import Decimal
from types import SimpleNamespace

import pytest

from crawler.tools.llm_anthropic import (
    DEFAULT_ANTHROPIC_MODEL,
    DEFAULT_ANTHROPIC_TEMPERATURE,
    OPUS_ESCALATION_MAX_TOKENS,
    OPUS_ESCALATION_MODEL,
    AnthropicError,
    AnthropicClient,
    MessagesClient,
    opus_escalation_config,
)


class FakeMessages:
    def __init__(self, response: object) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    async def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        return self.response


class FakeAnthropicSDK:
    def __init__(self, response: object) -> None:
        self.fake_messages = FakeMessages(response)
        self.messages: MessagesClient = self.fake_messages


def test_anthropic_request_uses_cache_control_on_static_system_prefix() -> None:
    async def run() -> None:
        response = SimpleNamespace(
            model=DEFAULT_ANTHROPIC_MODEL,
            content=[
                SimpleNamespace(type="tool_use", name="json_response", input={"name": "Neo Bank"})
            ],
            usage=SimpleNamespace(input_tokens=1000, output_tokens=200),
        )
        sdk = FakeAnthropicSDK(response)
        client = AnthropicClient(sdk)
        schema = {"type": "object", "properties": {"name": {"type": "string"}}}

        result = await client.generate("extract", system="dynamic", json_schema=schema)

        request = sdk.fake_messages.calls[0]
        assert request["model"] == DEFAULT_ANTHROPIC_MODEL
        assert request["temperature"] == DEFAULT_ANTHROPIC_TEMPERATURE
        assert request["messages"] == [
            {"role": "user", "content": [{"type": "text", "text": "extract"}]}
        ]
        assert request["tool_choice"] == {"type": "tool", "name": "json_response"}
        assert request["tools"] == [
            {
                "name": "json_response",
                "description": "Return the requested data as JSON matching the input schema.",
                "input_schema": schema,
            }
        ]
        system = request["system"]
        assert isinstance(system, list)
        assert system[0]["cache_control"] == {"type": "ephemeral"}
        assert system[0]["type"] == "text"
        assert system[1] == {"type": "text", "text": "dynamic"}
        assert result.json_value == {"name": "Neo Bank"}
        assert result.usage.input_tokens == 1000
        assert result.usage.output_tokens == 200
        assert result.usage.total_tokens == 1200
        assert result.usage.cost_usd == Decimal("0.006")

    asyncio.run(run())


def test_anthropic_parses_text_and_usage_from_mapping_response() -> None:
    async def run() -> None:
        sdk = FakeAnthropicSDK(
            {
                "model": "claude-sonnet-test",
                "content": [{"type": "text", "text": "done"}],
                "usage": {"input_tokens": 3, "output_tokens": 4},
            }
        )
        client = AnthropicClient(sdk, model="claude-sonnet-test")

        result = await client.generate("hello", static_system_prefix="")

        assert result.model == "claude-sonnet-test"
        assert result.text == "done"
        assert result.json_value is None
        assert result.usage.input_tokens == 3
        assert result.usage.output_tokens == 4
        assert result.usage.cost_usd == Decimal("0.000069")
        assert sdk.fake_messages.calls[0]["system"] == []

    asyncio.run(run())


def test_anthropic_opus_escalation_config_defaults() -> None:
    assert opus_escalation_config() == {
        "model": OPUS_ESCALATION_MODEL,
        "max_tokens": OPUS_ESCALATION_MAX_TOKENS,
        "temperature": DEFAULT_ANTHROPIC_TEMPERATURE,
    }


def test_anthropic_counts_prompt_cache_tokens() -> None:
    async def run() -> None:
        sdk = FakeAnthropicSDK(
            {
                "model": DEFAULT_ANTHROPIC_MODEL,
                "content": [{"type": "text", "text": "done"}],
                "usage": {
                    "input_tokens": 100,
                    "cache_creation_input_tokens": 20,
                    "cache_read_input_tokens": 30,
                    "output_tokens": 10,
                },
            }
        )

        result = await AnthropicClient(sdk).generate("hello")

        assert result.usage.input_tokens == 150
        assert result.usage.cache_creation_input_tokens == 20
        assert result.usage.cache_read_input_tokens == 30
        assert result.usage.total_tokens == 160
        assert result.usage.cost_usd == Decimal("0.000534")

    asyncio.run(run())


def test_anthropic_json_schema_requires_json_tool_result() -> None:
    async def run() -> None:
        sdk = FakeAnthropicSDK(
            {
                "model": DEFAULT_ANTHROPIC_MODEL,
                "content": [{"type": "text", "text": "{}"}],
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }
        )

        with pytest.raises(AnthropicError, match="JSON tool"):
            await AnthropicClient(sdk).generate("hello", json_schema={"type": "object"})

    asyncio.run(run())
