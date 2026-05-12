from __future__ import annotations

from collections.abc import Awaitable, Mapping
from decimal import Decimal
from inspect import isawaitable
from typing import Protocol, cast

from pydantic import BaseModel, ConfigDict, Field

from crawler.harness.checkpoint import JsonValue

DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-6"
DEFAULT_ANTHROPIC_TEMPERATURE = 0.0
OPUS_ESCALATION_MODEL = "claude-opus-4-7"
OPUS_ESCALATION_MAX_TOKENS = 8192
STATIC_SYSTEM_PREFIX = "You are a precise extraction assistant. Return only requested facts."
_INPUT_PRICE_PER_MTOK = Decimal("3")
_OUTPUT_PRICE_PER_MTOK = Decimal("15")
_OPUS_INPUT_PRICE_PER_MTOK = Decimal("15")
_OPUS_OUTPUT_PRICE_PER_MTOK = Decimal("75")
_CACHE_WRITE_MULTIPLIER = Decimal("1.25")
_CACHE_READ_MULTIPLIER = Decimal("0.1")


class AnthropicError(RuntimeError):
    """Raised when Anthropic returns an invalid response."""


class LLMUsage(BaseModel):
    model_config = ConfigDict(frozen=True)

    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cache_creation_input_tokens: int = Field(default=0, ge=0)
    cache_read_input_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    cost_usd: Decimal = Field(default=Decimal("0"), ge=Decimal("0"))


class LLMResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    model: str
    text: str
    json_value: JsonValue = None
    usage: LLMUsage = Field(default_factory=LLMUsage)
    raw: dict[str, JsonValue] = Field(default_factory=dict)


class MessagesClient(Protocol):
    def create(self, **kwargs: object) -> object | Awaitable[object]: ...


class AnthropicSDKClient(Protocol):
    messages: MessagesClient


class AnthropicClient:
    def __init__(
        self,
        client: AnthropicSDKClient,
        *,
        model: str = DEFAULT_ANTHROPIC_MODEL,
        max_tokens: int = 4096,
    ) -> None:
        self._client = client
        self._model = model
        self._max_tokens = max_tokens

    async def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        json_schema: Mapping[str, object] | None = None,
        model: str | None = None,
        temperature: float = DEFAULT_ANTHROPIC_TEMPERATURE,
        max_tokens: int | None = None,
        static_system_prefix: str = STATIC_SYSTEM_PREFIX,
    ) -> LLMResult:
        resolved_model = model or self._model
        request = _request_payload(
            prompt=prompt,
            model=resolved_model,
            max_tokens=max_tokens or self._max_tokens,
            temperature=temperature,
            system=system,
            json_schema=json_schema,
            static_system_prefix=static_system_prefix,
        )
        response = self._client.messages.create(**request)
        if isawaitable(response):
            response = await response
        return _parse_response(response=response, model=resolved_model, expect_json=json_schema is not None)


def opus_escalation_config() -> dict[str, object]:
    return {
        "model": OPUS_ESCALATION_MODEL,
        "max_tokens": OPUS_ESCALATION_MAX_TOKENS,
        "temperature": DEFAULT_ANTHROPIC_TEMPERATURE,
    }


def _request_payload(
    *,
    prompt: str,
    model: str,
    max_tokens: int,
    temperature: float,
    system: str | None,
    json_schema: Mapping[str, object] | None,
    static_system_prefix: str,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "system": _system_blocks(
            static_system_prefix=static_system_prefix,
            system=system,
        ),
        "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}]}],
    }
    if json_schema is not None:
        payload["tools"] = [
            {
                "name": "json_response",
                "description": "Return the requested data as JSON matching the input schema.",
                "input_schema": dict(json_schema),
            }
        ]
        payload["tool_choice"] = {"type": "tool", "name": "json_response"}
    return payload


def _system_blocks(*, static_system_prefix: str, system: str | None) -> list[dict[str, object]]:
    blocks: list[dict[str, object]] = []
    if static_system_prefix:
        blocks.append(
            {
                "type": "text",
                "text": static_system_prefix,
                "cache_control": {"type": "ephemeral"},
            }
        )
    if system:
        blocks.append({"type": "text", "text": system})
    return blocks


def _parse_response(*, response: object, model: str, expect_json: bool) -> LLMResult:
    content = _get(response, "content")
    if not isinstance(content, list):
        raise AnthropicError("Anthropic response did not include content blocks")

    texts: list[str] = []
    tool_blocks = 0
    json_value: JsonValue = None
    for block in content:
        block_type = _get(block, "type")
        if block_type == "text":
            text = _get(block, "text")
            if isinstance(text, str):
                texts.append(text)
        elif block_type == "tool_use":
            if _get(block, "name") != "json_response":
                raise AnthropicError("Anthropic response used an unexpected tool")
            tool_blocks += 1
            json_value = cast(JsonValue, _get(block, "input"))
    if expect_json and tool_blocks != 1:
        raise AnthropicError("Anthropic response did not include exactly one JSON tool result")

    usage = _usage(_get(response, "usage"), model=model)
    return LLMResult(
        model=str(_get(response, "model") or model),
        text="\n".join(texts),
        json_value=json_value,
        usage=usage,
        raw=_response_raw(response),
    )


def _usage(value: object, *, model: str) -> LLMUsage:
    input_tokens = _int_value(_get(value, "input_tokens"))
    output_tokens = _int_value(_get(value, "output_tokens"))
    cache_creation_tokens = _int_value(_get(value, "cache_creation_input_tokens"))
    cache_read_tokens = _int_value(_get(value, "cache_read_input_tokens"))
    billable_input_tokens = input_tokens + cache_creation_tokens + cache_read_tokens
    return LLMUsage(
        input_tokens=billable_input_tokens,
        output_tokens=output_tokens,
        cache_creation_input_tokens=cache_creation_tokens,
        cache_read_input_tokens=cache_read_tokens,
        total_tokens=billable_input_tokens + output_tokens,
        cost_usd=_cost(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_creation_input_tokens=cache_creation_tokens,
            cache_read_input_tokens=cache_read_tokens,
            model=model,
        ),
    )


def _cost(
    *,
    input_tokens: int,
    output_tokens: int,
    cache_creation_input_tokens: int,
    cache_read_input_tokens: int,
    model: str,
) -> Decimal:
    if "opus" in model:
        input_price = _OPUS_INPUT_PRICE_PER_MTOK
        output_price = _OPUS_OUTPUT_PRICE_PER_MTOK
    else:
        input_price = _INPUT_PRICE_PER_MTOK
        output_price = _OUTPUT_PRICE_PER_MTOK
    input_cost = Decimal(input_tokens) * input_price
    cache_write_cost = Decimal(cache_creation_input_tokens) * input_price * _CACHE_WRITE_MULTIPLIER
    cache_read_cost = Decimal(cache_read_input_tokens) * input_price * _CACHE_READ_MULTIPLIER
    output_cost = Decimal(output_tokens) * output_price
    return (input_cost + cache_write_cost + cache_read_cost + output_cost) / Decimal(1_000_000)


def _response_raw(response: object) -> dict[str, JsonValue]:
    model_dump = getattr(response, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump()
        if isinstance(dumped, Mapping):
            return {str(key): cast(JsonValue, value) for key, value in dumped.items()}
    return {}


def _get(value: object, key: str) -> object:
    if isinstance(value, Mapping):
        return value.get(key)
    return getattr(value, key, None)


def _int_value(value: object) -> int:
    return value if isinstance(value, int) and value >= 0 else 0
