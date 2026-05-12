from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from crawler.agents.base import AgentConfig, AgentInputEnvelope, AgentOutputEnvelope, SubAgent, ToolContext
from crawler.extractor.extract import DEFAULT_MAX_CONTENT_CHARS, build_prompt, schema_for_field_group
from crawler.extractor.router import ExtractionConfigError, ExtractionValidationError, LLMRouter, RouterAttempt
from crawler.harness.checkpoint import JsonValue
from crawler.tools.http import RawDoc

RAW_DOC_TOOL = "extract.raw_doc"


class ExtractionAgent(SubAgent):
    config = AgentConfig(
        name="extraction",
        role="field-group extraction",
        tool_names=(RAW_DOC_TOOL,),
        temperature=0,
    )

    def __init__(
        self,
        *,
        router: LLMRouter | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._router = router

    async def execute(
        self,
        envelope: AgentInputEnvelope,
        context: ToolContext,
    ) -> AgentOutputEnvelope:
        if self._router is None:
            raise ExtractionConfigError("ExtractionAgent requires an injected LLMRouter")
        field_group = _string_param(envelope.params, "field_group")
        schema = schema_for_field_group(field_group)
        document = await _raw_doc_from_params_or_context(envelope.params, context)
        max_content_chars = _int_param(
            envelope.params,
            "max_content_chars",
            default=DEFAULT_MAX_CONTENT_CHARS,
        )
        prompt = build_prompt(
            field_group=field_group,
            document=document,
            max_content_chars=max_content_chars,
        )
        routed = await self._router.extract(
            prompt=prompt.text,
            system="Extract only fields supported by the provided document. Never guess.",
            schema=schema,
            temperature=envelope.temperature,
        )
        extracted_json = _checked_extracted_json(routed.json_value, document, routed)
        return AgentOutputEnvelope(
            metadata={
                "field_group": field_group,
                "model": routed.model,
                "tier": routed.tier,
                "confidence": routed.confidence,
                "source_url": document.source_url,
                "content_hash": document.content_hash,
                "truncated": prompt.truncated,
                "original_length": prompt.original_length,
                "max_content_chars": prompt.max_content_chars,
                "usage": {
                    "tokens": routed.usage_tokens,
                    "usd": str(routed.usage_usd),
                },
                "attempts": [_attempt_json(attempt) for attempt in routed.attempts],
            },
            state={
                "field_group": field_group,
                "extracted": extracted_json,
                "provenance": {
                    "source_url": document.source_url,
                    "content_hash": document.content_hash,
                    "llm_model": routed.model,
                    "llm_tier": routed.tier,
                    "confidence": routed.confidence,
                },
            },
            turns=len(routed.attempts),
            tokens=routed.usage_tokens,
            usd=routed.usage_usd,
        )


async def _raw_doc_from_params_or_context(
    params: Mapping[str, JsonValue],
    context: ToolContext,
) -> RawDoc:
    for key in ("raw_doc", "document"):
        value = params.get(key)
        if value is not None:
            return _coerce_raw_doc(value)

    tool_kwargs = {
        key: value
        for key, value in params.items()
        if key not in {"field_group", "max_content_chars", "raw_doc", "document"}
    }
    tool_result = await context.call_tool(RAW_DOC_TOOL, **tool_kwargs)
    return _coerce_raw_doc(tool_result)


def _coerce_raw_doc(value: object) -> RawDoc:
    if isinstance(value, RawDoc):
        return value
    if isinstance(value, Mapping):
        return RawDoc.model_validate(dict(value))
    raise TypeError(f"ExtractionAgent requires RawDoc-ish input, got {type(value).__name__}")


def _string_param(params: Mapping[str, JsonValue], key: str) -> str:
    value = params.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"ExtractionAgent requires string param {key!r}")
    return value


def _int_param(params: Mapping[str, JsonValue], key: str, *, default: int) -> int:
    value = params.get(key, default)
    if isinstance(value, int) and value > 0:
        return value
    raise ValueError(f"ExtractionAgent requires positive integer param {key!r}")


def _attempt_json(attempt: RouterAttempt) -> dict[str, JsonValue]:
    return {
        "tier": attempt.tier,
        "model": attempt.model,
        "valid": attempt.valid,
        "confidence": attempt.confidence,
        "error": attempt.error,
    }


def _checked_extracted_json(value: dict[str, JsonValue], document: RawDoc, routed: Any) -> dict[str, JsonValue]:
    source_text = document.text or (document.bytes.decode("utf-8", errors="replace") if document.bytes else "")
    for key, child in value.items():
        if isinstance(child, str) and _looks_like_raw_source(child, source_text):
            raise ExtractionValidationError(
                f"Extracted field {key!r} appears to contain raw source content",
                attempts=routed.attempts,
                usage_tokens=routed.usage_tokens,
                usage_usd=routed.usage_usd,
            )
    return value


def _looks_like_raw_source(value: str, source_text: str) -> bool:
    normalized = value.strip()
    lowered = normalized.lower()
    if "<html" in lowered or "</body" in lowered or "</div" in lowered or "<script" in lowered:
        return True
    if len(normalized) > 800:
        return True
    return len(normalized) > 160 and normalized in source_text
