from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any, Protocol

from crawler.agents.base import AgentConfig, AgentInputEnvelope, AgentOutputEnvelope, SubAgent, ToolContext
from crawler.harness.checkpoint import JsonValue
from crawler.store.backend_client import BackendUpsertResult
from crawler.utils.guards import is_raw_key, looks_like_raw_value

BACKEND_UPSERT_TOOL = "backend.upsert"


class WriterAgent(SubAgent):
    config = AgentConfig(
        name="writer",
        role="validated backend writer",
        tool_names=(BACKEND_UPSERT_TOOL,),
        temperature=0,
    )

    def __init__(self, *, backend_client: BackendWriter | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._backend_client = backend_client

    async def execute(
        self,
        envelope: AgentInputEnvelope,
        context: ToolContext,
    ) -> AgentOutputEnvelope:
        decision = _optional_string(envelope.params.get("decision"))
        write_payload = _mapping_param(envelope.params.get("write_payload"))
        if decision != "auto_apply":
            return _skipped("validation_not_auto_apply", decision=decision)
        if not write_payload:
            return _skipped("empty_write_payload", decision=decision)
        cleaned_payload = _mapping_param(_clean_json(write_payload))
        if not cleaned_payload:
            return _skipped("empty_write_payload_after_cleaning", decision=decision)

        request: dict[str, JsonValue] = {
            "field_group": _optional_string(envelope.params.get("field_group")),
            "bank_id": envelope.params.get("bank_id"),
            "sort_id": envelope.params.get("sort_id"),
            "write_payload": cleaned_payload,
            "provenance": _clean_json(_mapping_param(envelope.params.get("provenance"))),
            "review_queue": _clean_json(_list_param(envelope.params.get("review_queue"))),
            "dedup_key": envelope.dedup_key,
        }
        result = await self._upsert(request, context)
        result_json = _result_json(result)
        write_keys: list[JsonValue] = [str(key) for key in sorted(cleaned_payload)]
        return AgentOutputEnvelope(
            metadata={
                "decision": decision,
                "applied": result.applied,
                "needs_review": result.needs_review,
                "review_count": len(result.review_queue),
                "record_id": result.record_id,
                "version": result.version,
            },
            state={
                "field_group": request["field_group"],
                "write_keys": write_keys,
                "backend_result": _clean_json(result_json),
                "provenance": request["provenance"],
                "review_queue": request["review_queue"],
            },
            turns=1,
            tokens=0,
            usd=Decimal("0"),
        )

    async def _upsert(
        self,
        request: Mapping[str, JsonValue],
        context: ToolContext,
    ) -> BackendUpsertResult:
        if self._backend_client is not None:
            return await self._backend_client.upsert(request)
        result = await context.call_tool(BACKEND_UPSERT_TOOL, request)
        if isinstance(result, BackendUpsertResult):
            return result
        if isinstance(result, Mapping):
            return BackendUpsertResult.model_validate(result)
        raise TypeError(f"Backend upsert returned unsupported {type(result).__name__}")


class BackendWriter(Protocol):
    async def upsert(self, payload: Mapping[str, JsonValue]) -> BackendUpsertResult: ...


def _skipped(reason: str, *, decision: str | None) -> AgentOutputEnvelope:
    return AgentOutputEnvelope(
        metadata={
            "decision": decision,
            "applied": False,
            "needs_review": decision == "needs_review",
            "skipped_reason": reason,
        },
        state={"skipped_reason": reason},
    )


def _result_json(result: BackendUpsertResult) -> dict[str, JsonValue]:
    dumped = result.model_dump(mode="json")
    return dumped if isinstance(dumped, dict) else {}


def _mapping_param(value: JsonValue | None) -> dict[str, JsonValue]:
    return {str(key): _json_or_none(child) for key, child in value.items()} if isinstance(value, Mapping) else {}


def _list_param(value: JsonValue | None) -> list[JsonValue]:
    return [_json_or_none(item) for item in value] if isinstance(value, list) else []


def _optional_string(value: JsonValue | None) -> str | None:
    return value if isinstance(value, str) and value else None


def _clean_json(value: JsonValue) -> JsonValue:
    if isinstance(value, Mapping):
        return {
            str(key): _clean_json(child)
            for key, child in sorted(value.items(), key=lambda item: str(item[0]))
            if not _is_raw_key(str(key)) and not _looks_like_raw_value(_json_or_none(child))
        }
    if isinstance(value, list):
        return [_clean_json(child) for child in value if not _looks_like_raw_value(_json_or_none(child))]
    return _json_or_none(value)


def _json_or_none(value: object) -> JsonValue:
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    if isinstance(value, list):
        return [_json_or_none(child) for child in value]
    if isinstance(value, Mapping):
        return {str(key): _json_or_none(child) for key, child in value.items()}
    return str(value)


_is_raw_key = is_raw_key
_looks_like_raw_value = looks_like_raw_value
