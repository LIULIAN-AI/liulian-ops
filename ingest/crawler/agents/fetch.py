from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from crawler.agents.base import AgentConfig, AgentInputEnvelope, AgentOutputEnvelope, SubAgent, ToolContext
from crawler.harness.checkpoint import JsonValue
from crawler.harness.distill import redact_json
from crawler.tools.http import RawDoc


FETCH_TOOL_BY_SOURCE_TYPE = {
    "http": "fetch.http",
    "browser": "fetch.browser",
    "pdf": "fetch.pdf",
    "wikipedia": "fetch.wikipedia",
    "app_store": "fetch.app_store",
    "regulator": "fetch.regulator",
}


class FetchAgent(SubAgent):
    config = AgentConfig(
        name="fetch",
        role="offline fetch router",
        tool_names=tuple(FETCH_TOOL_BY_SOURCE_TYPE.values()),
    )

    async def execute(
        self,
        envelope: AgentInputEnvelope,
        context: ToolContext,
    ) -> AgentOutputEnvelope:
        source_type = _string_param(envelope.params, "source_type").lower()
        try:
            tool_name = FETCH_TOOL_BY_SOURCE_TYPE[source_type]
        except KeyError as exc:
            raise ValueError(f"Unsupported fetch source_type: {source_type!r}") from exc

        document = _coerce_raw_doc(await context.call_tool(tool_name, **_tool_kwargs(envelope.params)))
        return AgentOutputEnvelope(
            metadata=_summary_metadata(document, source_type=source_type, tool_name=tool_name),
            state={
                "source_url": document.source_url,
                "content_type": document.content_type,
                "content_hash": document.content_hash,
            },
            turns=1,
            tokens=0,
            usd=Decimal("0"),
        )


def _tool_kwargs(params: Mapping[str, JsonValue]) -> dict[str, object]:
    return {key: value for key, value in params.items() if key not in {"source_type"}}


def _summary_metadata(document: RawDoc, *, source_type: str, tool_name: str) -> dict[str, JsonValue]:
    return {
        "source_type": source_type,
        "tool_name": tool_name,
        "source_url": document.source_url,
        "content_type": document.content_type,
        "content_hash": document.content_hash,
        "text_length": len(document.text or ""),
        "bytes_length": len(document.bytes or b""),
        "tool_metadata": _safe_tool_metadata(document.metadata),
    }


def _safe_tool_metadata(metadata: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    safe = _omit_long_strings(redact_json(dict(metadata)))
    return safe if isinstance(safe, dict) else {}


def _omit_long_strings(value: JsonValue) -> JsonValue:
    if isinstance(value, str) and len(value) > 500:
        return f"[omitted:{len(value)} chars]"
    if isinstance(value, dict):
        return {key: _omit_long_strings(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_omit_long_strings(child) for child in value]
    return value


def _coerce_raw_doc(value: object) -> RawDoc:
    if isinstance(value, RawDoc):
        return value
    if isinstance(value, Mapping):
        return RawDoc.model_validate(dict(value))
    raise TypeError(f"Fetch tool returned unsupported value: {type(value).__name__}")


def _string_param(params: Mapping[str, Any], key: str) -> str:
    value = params.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"FetchAgent requires string param {key!r}")
    return value
