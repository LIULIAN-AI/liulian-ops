from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
import json
from typing import Protocol, TypeVar, cast

from pydantic import BaseModel, ValidationError

from crawler.harness.checkpoint import JsonValue
from crawler.tools.llm_anthropic import opus_escalation_config

TModel = TypeVar("TModel", bound=BaseModel)


class ExtractionError(RuntimeError):
    """Base class for extraction failures."""


class ExtractionConfigError(ExtractionError):
    """Raised when extraction is configured with unsupported options."""


class ExtractionLLMError(ExtractionError):
    """Raised when every configured LLM tier fails to produce usable output."""


class ExtractionValidationError(ExtractionError):
    """Raised when an LLM response cannot be validated against the extraction schema."""

    def __init__(
        self,
        message: str,
        *,
        attempts: tuple[RouterAttempt, ...] = (),
        usage_tokens: int = 0,
        usage_usd: Decimal = Decimal("0"),
    ) -> None:
        super().__init__(message)
        self.attempts = attempts
        self.usage_tokens = usage_tokens
        self.usage_usd = usage_usd


class ExtractionLowConfidenceError(ExtractionValidationError):
    """Raised when validated output is below the configured confidence threshold."""


class LLMUsageLike(Protocol):
    @property
    def input_tokens(self) -> int: ...

    @property
    def output_tokens(self) -> int: ...

    @property
    def total_tokens(self) -> int: ...

    @property
    def cost_usd(self) -> Decimal: ...


class LLMResultLike(Protocol):
    @property
    def model(self) -> str: ...

    @property
    def text(self) -> str: ...

    @property
    def json_value(self) -> JsonValue: ...

    @property
    def usage(self) -> LLMUsageLike: ...


class LLMClient(Protocol):
    async def generate(self, prompt: str, **kwargs: object) -> LLMResultLike: ...


@dataclass(frozen=True)
class RouterAttempt:
    tier: str
    model: str | None
    valid: bool
    confidence: float | None
    error: str | None


@dataclass(frozen=True)
class RoutedExtraction:
    value: BaseModel
    json_value: dict[str, JsonValue]
    model: str
    tier: str
    confidence: float
    usage_tokens: int
    usage_usd: Decimal
    attempts: tuple[RouterAttempt, ...]


@dataclass(frozen=True)
class OpusEscalation:
    enabled: bool = False
    model: str | None = None
    max_tokens: int | None = None


class LLMRouter:
    def __init__(
        self,
        *,
        ollama: LLMClient | None,
        anthropic: LLMClient | None,
        confidence_threshold: float = 0.65,
        opus: OpusEscalation | None = None,
    ) -> None:
        if ollama is None and anthropic is None:
            raise ExtractionConfigError("LLMRouter requires at least one LLM client")
        self._ollama = ollama
        self._anthropic = anthropic
        self._confidence_threshold = confidence_threshold
        self._opus = opus or OpusEscalation()

    async def extract(
        self,
        *,
        prompt: str,
        system: str,
        schema: type[TModel],
        temperature: float = 0,
    ) -> RoutedExtraction:
        json_schema = cast(Mapping[str, object], schema.model_json_schema())
        attempts: list[RouterAttempt] = []
        total_tokens = 0
        total_usd = Decimal("0")

        if self._ollama is not None:
            result = await self._try_tier(
                self._ollama,
                tier="ollama",
                prompt=prompt,
                system=system,
                json_schema=json_schema,
                schema=schema,
                temperature=temperature,
                attempts=attempts,
            )
            total_tokens += result[1]
            total_usd += result[2]
            if result[0] is not None:
                routed = result[0]
                return _with_usage(routed, tokens=total_tokens, usd=total_usd, attempts=attempts)

        if self._anthropic is not None:
            result = await self._try_tier(
                self._anthropic,
                tier="anthropic_sonnet",
                prompt=prompt,
                system=system,
                json_schema=json_schema,
                schema=schema,
                temperature=temperature,
                attempts=attempts,
            )
            total_tokens += result[1]
            total_usd += result[2]
            if result[0] is not None:
                routed = result[0]
                return _with_usage(routed, tokens=total_tokens, usd=total_usd, attempts=attempts)

            if self._opus.enabled:
                config = opus_escalation_config()
                opus_model = self._opus.model or str(config["model"])
                opus_max_tokens = self._opus.max_tokens or _int_config(config["max_tokens"])
                result = await self._try_tier(
                    self._anthropic,
                    tier="anthropic_opus",
                    prompt=prompt,
                    system=system,
                    json_schema=json_schema,
                    schema=schema,
                    temperature=temperature,
                    attempts=attempts,
                    model=opus_model,
                    max_tokens=opus_max_tokens,
                )
                total_tokens += result[1]
                total_usd += result[2]
                if result[0] is not None:
                    routed = result[0]
                    return _with_usage(routed, tokens=total_tokens, usd=total_usd, attempts=attempts)

        raise ExtractionValidationError(
            _attempt_error_message(attempts),
            attempts=tuple(attempts),
            usage_tokens=total_tokens,
            usage_usd=total_usd,
        )

    async def _try_tier(
        self,
        client: LLMClient,
        *,
        tier: str,
        prompt: str,
        system: str,
        json_schema: Mapping[str, object],
        schema: type[TModel],
        temperature: float,
        attempts: list[RouterAttempt],
        model: str | None = None,
        max_tokens: int | None = None,
    ) -> tuple[RoutedExtraction | None, int, Decimal]:
        try:
            kwargs: dict[str, object] = {
                "system": system,
                "json_schema": json_schema,
                "model": model,
                "temperature": temperature,
            }
            if max_tokens is not None:
                kwargs["max_tokens"] = max_tokens
            result = await client.generate(prompt, **kwargs)
        except Exception as exc:
            attempts.append(
                RouterAttempt(
                    tier=tier,
                    model=model,
                    valid=False,
                    confidence=None,
                    error=_sanitize_error(exc),
                )
            )
            return None, 0, Decimal("0")

        tokens = result.usage.total_tokens
        usd = result.usage.cost_usd
        try:
            value = _validate_result(result, schema)
            json_value = _dump_json(value)
            confidence = _confidence(json_value)
            if confidence < self._confidence_threshold:
                raise ExtractionLowConfidenceError(
                    f"{tier} confidence {confidence:.3f} below threshold "
                    f"{self._confidence_threshold:.3f}"
                )
        except ExtractionValidationError as exc:
            attempts.append(
                RouterAttempt(
                    tier=tier,
                    model=result.model,
                    valid=False,
                    confidence=_safe_confidence(result),
                    error=_sanitize_error(exc),
                )
            )
            return None, tokens, usd

        attempts.append(
            RouterAttempt(
                tier=tier,
                model=result.model,
                valid=True,
                confidence=confidence,
                error=None,
            )
        )
        return (
            RoutedExtraction(
                value=value,
                json_value=json_value,
                model=result.model,
                tier=tier,
                confidence=confidence,
                usage_tokens=tokens,
                usage_usd=usd,
                attempts=tuple(attempts),
            ),
            tokens,
            usd,
        )


def _validate_result(result: LLMResultLike, schema: type[TModel]) -> TModel:
    payload = result.json_value
    if payload is None:
        payload = _parse_text_json(result.text)
    try:
        return schema.model_validate(payload)
    except ValidationError as exc:
        raise ExtractionValidationError(str(exc)) from exc


def _parse_text_json(text: str) -> JsonValue:
    try:
        return cast(JsonValue, json.loads(text))
    except json.JSONDecodeError as exc:
        raise ExtractionValidationError("LLM response was not valid JSON") from exc


def _dump_json(value: BaseModel) -> dict[str, JsonValue]:
    dumped = value.model_dump(mode="json")
    if not isinstance(dumped, dict):
        raise ExtractionValidationError("Validated extraction did not serialize as an object")
    return cast(dict[str, JsonValue], dumped)


def _confidence(value: Mapping[str, JsonValue]) -> float:
    raw = value.get("confidence")
    if isinstance(raw, int | float):
        return float(raw)
    raise ExtractionValidationError("Validated extraction did not include numeric confidence")


def _safe_confidence(result: LLMResultLike) -> float | None:
    try:
        payload = result.json_value if result.json_value is not None else _parse_text_json(result.text)
    except ExtractionValidationError:
        return None
    if isinstance(payload, Mapping):
        raw = payload.get("confidence")
        if isinstance(raw, int | float):
            return float(raw)
    return None


def _with_usage(
    routed: RoutedExtraction,
    *,
    tokens: int,
    usd: Decimal,
    attempts: list[RouterAttempt],
) -> RoutedExtraction:
    return RoutedExtraction(
        value=routed.value,
        json_value=routed.json_value,
        model=routed.model,
        tier=routed.tier,
        confidence=routed.confidence,
        usage_tokens=tokens,
        usage_usd=usd,
        attempts=tuple(attempts),
    )


def _attempt_error_message(attempts: list[RouterAttempt]) -> str:
    if not attempts:
        return "No LLM tier was available for extraction"
    tail = attempts[-1]
    return f"Extraction failed after {len(attempts)} attempt(s); last {tail.tier}: {tail.error}"


def _sanitize_error(exc: BaseException) -> str:
    if isinstance(exc, ExtractionLowConfidenceError):
        return str(exc)
    if isinstance(exc, ExtractionValidationError):
        return exc.__class__.__name__
    return exc.__class__.__name__


def _int_config(value: object) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value)
    raise ExtractionConfigError(f"Expected integer Opus config value, got {type(value).__name__}")
