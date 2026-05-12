from __future__ import annotations

import asyncio
from collections.abc import Mapping

import pytest

from crawler.tools.llm_ollama import (
    DEFAULT_OLLAMA_MODEL,
    DEFAULT_OLLAMA_TEMPERATURE,
    OllamaClient,
    OllamaError,
)


class FakeResponse:
    def __init__(self, status_code: int, body: object) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> object:
        return self._body


class FakeTransport:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.calls: list[tuple[str, Mapping[str, object], float]] = []

    async def post(
        self,
        url: str,
        *,
        json: Mapping[str, object],
        timeout: float,
    ) -> FakeResponse:
        self.calls.append((url, json, timeout))
        return self.response


def test_ollama_request_and_parse_json_schema_response() -> None:
    async def run() -> None:
        schema = {"type": "object", "properties": {"name": {"type": "string"}}}
        transport = FakeTransport(
            FakeResponse(
                200,
                {
                    "model": "llama3.1",
                    "response": '{"name":"Neo Bank"}',
                    "prompt_eval_count": 7,
                    "eval_count": 5,
                },
            )
        )
        client = OllamaClient(transport, base_url="http://ollama.test", timeout=12)

        result = await client.generate("extract", system="system", json_schema=schema)

        url, payload, timeout = transport.calls[0]
        assert url == "http://ollama.test/api/generate"
        assert payload["model"] == DEFAULT_OLLAMA_MODEL
        assert payload["prompt"] == "extract"
        assert payload["system"] == "system"
        assert payload["stream"] is False
        assert payload["format"] == schema
        assert payload["options"] == {"temperature": DEFAULT_OLLAMA_TEMPERATURE}
        assert timeout == 12
        assert result.text == '{"name":"Neo Bank"}'
        assert result.json_value == {"name": "Neo Bank"}
        assert result.usage.input_tokens == 7
        assert result.usage.output_tokens == 5
        assert result.usage.total_tokens == 12
        assert result.usage.cost_usd == 0

    asyncio.run(run())


def test_ollama_error_response_raises() -> None:
    async def run() -> None:
        transport = FakeTransport(FakeResponse(500, {"error": "model unavailable"}))
        client = OllamaClient(transport)

        with pytest.raises(OllamaError, match="model unavailable"):
            await client.generate("hello")

    asyncio.run(run())


def test_ollama_rejects_non_object_response() -> None:
    async def run() -> None:
        transport = FakeTransport(FakeResponse(200, ["not", "object"]))
        client = OllamaClient(transport)

        with pytest.raises(OllamaError, match="JSON object"):
            await client.generate("hello")

    asyncio.run(run())


def test_ollama_wraps_malformed_structured_json() -> None:
    async def run() -> None:
        transport = FakeTransport(FakeResponse(200, {"response": "not-json"}))
        client = OllamaClient(transport)

        with pytest.raises(OllamaError, match="invalid JSON"):
            await client.generate("hello", json_schema={"type": "object"})

    asyncio.run(run())


def test_ollama_validates_basic_json_schema_shape() -> None:
    async def run() -> None:
        transport = FakeTransport(FakeResponse(200, {"response": "[]"}))
        client = OllamaClient(transport)

        with pytest.raises(OllamaError, match="schema type object"):
            await client.generate("hello", json_schema={"type": "object"})

    asyncio.run(run())
