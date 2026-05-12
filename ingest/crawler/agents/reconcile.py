from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from decimal import Decimal
from json import dumps
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from crawler.agents.base import AgentConfig, AgentInputEnvelope, AgentOutputEnvelope, SubAgent, ToolContext
from crawler.harness.checkpoint import JsonValue
from crawler.utils.guards import is_raw_key, looks_like_raw_value

FieldDecision = Literal["accepted", "conflict_review"]


class FieldCandidate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    field_name: str = Field(min_length=1)
    value: JsonValue
    confidence: float = Field(..., ge=0.0, le=1.0)
    source_url: str | None = None
    snapshot_uri: str | None = None
    content_hash: str | None = None
    llm_model: str | None = None
    source_id: str | None = None

    @field_validator("field_name")
    @classmethod
    def _field_name_not_raw(cls, value: str) -> str:
        if _is_raw_key(value):
            raise ValueError(f"Raw source field {value!r} is not reconcilable")
        return value

    @field_validator("value")
    @classmethod
    def _value_not_raw(cls, value: JsonValue) -> JsonValue:
        if _looks_like_raw_value(value):
            raise ValueError("Candidate value appears to contain raw source content")
        return value


class ReconciledField(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    field_name: str
    value: JsonValue
    confidence: float = Field(..., ge=0.0, le=1.0)
    decision: FieldDecision
    reason: str
    sources: tuple[FieldCandidate, ...]
    alternatives: tuple[FieldCandidate, ...] = ()


class ReconcileAgent(SubAgent):
    config = AgentConfig(name="reconcile", role="multi-source field reconciliation", temperature=0)

    async def execute(
        self,
        envelope: AgentInputEnvelope,
        context: ToolContext,
    ) -> AgentOutputEnvelope:
        _ = context
        field_group = _string_param(envelope.params, "field_group")
        candidates = tuple(_candidate_inputs(envelope.params))
        grouped: dict[str, list[FieldCandidate]] = defaultdict(list)
        dropped = 0
        for raw in candidates:
            try:
                candidate = FieldCandidate.model_validate(raw)
            except ValueError:
                dropped += 1
                continue
            if candidate.value is not None:
                grouped[candidate.field_name].append(candidate)

        reconciled = tuple(_reconcile_field(name, values) for name, values in sorted(grouped.items()))
        record = {field.field_name: field.value for field in reconciled}
        if reconciled and "confidence" not in record:
            record["confidence"] = round(min(field.confidence for field in reconciled), 6)

        return AgentOutputEnvelope(
            metadata={
                "field_group": field_group,
                "candidate_count": len(candidates),
                "field_count": len(reconciled),
                "conflict_count": sum(1 for field in reconciled if field.decision == "conflict_review"),
                "dropped_raw_or_invalid_count": dropped,
            },
            state={
                "field_group": field_group,
                "normalized_record": record,
                "reconciled_fields": [_field_json(field) for field in reconciled],
            },
            turns=1,
            tokens=0,
            usd=Decimal("0"),
        )


def _reconcile_field(field_name: str, candidates: list[FieldCandidate]) -> ReconciledField:
    option_groups: dict[str, list[FieldCandidate]] = defaultdict(list)
    for candidate in candidates:
        option_groups[_canonical(candidate.value)].append(candidate)
    ranked = sorted(option_groups.values(), key=_option_sort_key)
    selected = ranked[0]
    alternatives = tuple(candidate for group in ranked[1:] for candidate in group)
    has_conflict = bool(alternatives)
    return ReconciledField(
        field_name=field_name,
        value=selected[0].value,
        confidence=_aggregate_confidence(selected),
        decision="conflict_review" if has_conflict else "accepted",
        reason="conflicting_values" if has_conflict else "single_value_or_consensus",
        sources=tuple(sorted(selected, key=_candidate_sort_key)),
        alternatives=tuple(sorted(alternatives, key=_candidate_sort_key)),
    )


def _candidate_inputs(params: Mapping[str, JsonValue]) -> Iterable[dict[str, JsonValue]]:
    for key in ("field_candidates", "candidates", "extractions"):
        value = params.get(key)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, Mapping):
                    yield from _expand_candidate_mapping(item)
            return
    yield from _expand_candidate_mapping(params)


def _expand_candidate_mapping(item: Mapping[str, object]) -> Iterable[dict[str, JsonValue]]:
    if "field_name" in item and "value" in item:
        yield _safe_candidate_dict(item)
        return
    extracted = item.get("extracted")
    if not isinstance(extracted, Mapping):
        extracted = item.get("normalized_record")
    if not isinstance(extracted, Mapping):
        return
    provenance = item.get("provenance")
    provenance_map = provenance if isinstance(provenance, Mapping) else {}
    confidence = _float_or_default(
        item.get("confidence"),
        _float_or_default(provenance_map.get("confidence"), _float_or_default(extracted.get("confidence"), 0.0)),
    )
    for field_name, value in extracted.items():
        if field_name == "confidence" or _is_raw_key(str(field_name)):
            continue
        yield {
            "field_name": str(field_name),
            "value": _json_or_none(value),
            "confidence": confidence,
            "source_url": _optional_string(provenance_map.get("source_url") or item.get("source_url")),
            "snapshot_uri": _optional_string(provenance_map.get("snapshot_uri") or item.get("snapshot_uri")),
            "content_hash": _optional_string(provenance_map.get("content_hash") or item.get("content_hash")),
            "llm_model": _optional_string(provenance_map.get("llm_model") or item.get("llm_model")),
            "source_id": _optional_string(provenance_map.get("source_id") or item.get("source_id")),
        }


def _safe_candidate_dict(item: Mapping[str, object]) -> dict[str, JsonValue]:
    return {
        "field_name": str(item["field_name"]),
        "value": _json_or_none(item.get("value")),
        "confidence": _float_or_default(item.get("confidence"), 0.0),
        "source_url": _optional_string(item.get("source_url")),
        "snapshot_uri": _optional_string(item.get("snapshot_uri")),
        "content_hash": _optional_string(item.get("content_hash")),
        "llm_model": _optional_string(item.get("llm_model")),
        "source_id": _optional_string(item.get("source_id")),
    }


def _option_sort_key(candidates: list[FieldCandidate]) -> tuple[int, float, str]:
    return (-len(candidates), -_aggregate_confidence(candidates), _canonical(candidates[0].value))


def _candidate_sort_key(candidate: FieldCandidate) -> tuple[float, str, str, str]:
    return (-candidate.confidence, candidate.source_url or "", candidate.content_hash or "", candidate.source_id or "")


def _aggregate_confidence(candidates: list[FieldCandidate]) -> float:
    confidence = sum(candidate.confidence for candidate in candidates) / len(candidates)
    support_bonus = min(0.1, 0.03 * (len(candidates) - 1))
    return round(min(1.0, confidence + support_bonus), 6)


def _field_json(field: ReconciledField) -> dict[str, JsonValue]:
    dumped = field.model_dump(mode="json")
    return dumped if isinstance(dumped, dict) else {}


def _canonical(value: JsonValue) -> str:
    return dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _string_param(params: Mapping[str, JsonValue], key: str) -> str:
    value = params.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"ReconcileAgent requires string param {key!r}")
    return value


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _float_or_default(value: object, default: float) -> float:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return max(0.0, min(1.0, float(value)))
    return default


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
