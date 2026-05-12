from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Protocol, cast

from pydantic import BaseModel, ConfigDict, Field

from crawler.harness.checkpoint import JsonValue

DEFAULT_OLLAMA_MODEL = "llama3.1"
DEFAULT_OLLAMA_TEMPERATURE = 0.0


class OllamaError(RuntimeError):
    """Raised when Ollama returns an error or an invalid response."""


class LLMUsage(BaseModel):
    model_config = ConfigDict(frozen=True)

    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    cost_usd: Decimal = Field(default=Decimal("0"), ge=Decimal("0"))


class LLMResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    model: str
    text: str
    json_value: JsonValue = None
    usage: LLMUsage = Field(default_factory=LLMUsage)
    raw: dict[str, JsonValue] = Field(default_factory=dict)


class HTTPResponse(Protocol):
    status_code: int

    def json(self) -> object: ...


class AsyncHTTPTransport(Protocol):
    async def post(
        self,
        url: str,
        *,
        json: Mapping[str, object],
        timeout: float,
    ) -> HTTPResponse: ...


class OllamaClient:
    def __init__(
        self,
        transport: AsyncHTTPTransport,
        *,
        base_url: str = "http://localhost:11434",
        model: str = DEFAULT_OLLAMA_MODEL,
        timeout: float = 60,
    ) -> None:
        self._transport = transport
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout

    async def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        json_schema: Mapping[str, object] | None = None,
        model: str | None = None,
        temperature: float = DEFAULT_OLLAMA_TEMPERATURE,
    ) -> LLMResult:
        resolved_model = model or self._model
        payload: dict[str, object] = {
            "model": resolved_model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": temperature},
        }
        if system is not None:
            payload["system"] = system
        if json_schema is not None:
            payload["format"] = dict(json_schema)

        response = await self._transport.post(
            f"{self._base_url}/api/generate",
            json=payload,
            timeout=self._timeout,
        )
        response_json = response.json()
        if not isinstance(response_json, Mapping):
            raise OllamaError("Ollama response was not a JSON object")
        raw = _json_mapping(response_json)
        if response.status_code >= 400:
            message = raw.get("error", f"Ollama request failed with status {response.status_code}")
            raise OllamaError(str(message))
        if "error" in raw:
            raise OllamaError(str(raw["error"]))

        text = raw.get("response")
        if not isinstance(text, str):
            raise OllamaError("Ollama response did not include text")

        return LLMResult(
            model=str(raw.get("model", resolved_model)),
            text=text,
            json_value=_parse_json_if_requested(text, json_schema),
            usage=LLMUsage(
                input_tokens=_int_value(raw.get("prompt_eval_count")),
                output_tokens=_int_value(raw.get("eval_count")),
                total_tokens=_int_value(raw.get("prompt_eval_count"))
                + _int_value(raw.get("eval_count")),
            ),
            raw=raw,
        )


def _parse_json_if_requested(text: str, json_schema: Mapping[str, object] | None) -> JsonValue:
    if json_schema is None:
        return None
    import json

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise OllamaError(f"Ollama returned invalid JSON: {exc}") from exc
    _validate_json_shape(parsed, json_schema)
    return cast(JsonValue, parsed)


def _validate_json_shape(value: object, schema: Mapping[str, object]) -> None:
    expected_type = schema.get("type")
    if expected_type == "object" and not isinstance(value, Mapping):
        raise OllamaError("Ollama JSON response did not match schema type object")
    if expected_type == "array" and not isinstance(value, list):
        raise OllamaError("Ollama JSON response did not match schema type array")
    if expected_type == "string" and not isinstance(value, str):
        raise OllamaError("Ollama JSON response did not match schema type string")
    required = schema.get("required")
    if isinstance(required, list) and isinstance(value, Mapping):
        missing = [item for item in required if isinstance(item, str) and item not in value]
        if missing:
            raise OllamaError(f"Ollama JSON response missing required keys: {missing}")


def _int_value(value: object) -> int:
    return value if isinstance(value, int) and value >= 0 else 0


def _json_mapping(value: Mapping[str, object]) -> dict[str, JsonValue]:
    return {str(key): cast(JsonValue, item) for key, item in value.items()}
