from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from crawler.agents.base import AgentConfig, AgentInputEnvelope, AgentOutputEnvelope, SubAgent, ToolContext
from crawler.extractor.extract import schema_for_field_group
from crawler.extractor.router import ExtractionConfigError
from crawler.hardening import ConfidencePolicy
from crawler.harness.checkpoint import JsonValue
from crawler.utils.guards import is_raw_key, looks_like_raw_value

ValidationDecision = Literal["auto_apply", "needs_review", "invalid"]
_SWIFT_RE = re.compile(r"^[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}([A-Z0-9]{3})?$")
_ISO2_RE = re.compile(r"^[A-Z]{2}$")
_COUNTRY_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z .'-]{1,80}$")


class ReviewQueueItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    field: str
    proposed_value: JsonValue
    current_value: JsonValue = None
    confidence: float = Field(..., ge=0.0, le=1.0)
    reason: str
    status: Literal["pending"] = "pending"


class ValidationIssue(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    field: str
    reason: str


class ValidationAgent(SubAgent):
    config = AgentConfig(name="validation", role="normalized payload validation", temperature=0)

    async def execute(
        self,
        envelope: AgentInputEnvelope,
        context: ToolContext,
    ) -> AgentOutputEnvelope:
        _ = context
        field_group = _string_param(envelope.params, "field_group")
        threshold = _threshold(envelope.params.get("confidence_threshold"), default=0.75)
        policy = ConfidencePolicy(auto_apply_threshold=threshold, invalid_threshold=0)
        raw_issues = _raw_input_issues(envelope.params)
        record = _record_from_params(envelope.params)
        fields = _reconciled_fields(envelope.params)
        current = _clean_record(_mapping_param(envelope.params.get("current_record")))

        issues = raw_issues + _business_rule_issues(record)
        review_items = _review_items(
            fields,
            record=record,
            current=current,
            policy=policy,
        )
        schema_issues = _schema_issues(field_group, record)
        issues.extend(schema_issues)

        decision: ValidationDecision
        if issues:
            decision = "invalid"
            write_payload: dict[str, JsonValue] = {}
        elif review_items:
            decision = "needs_review"
            write_payload = {}
        else:
            decision = "auto_apply"
            write_payload = record

        output_record = record if decision != "invalid" else {}
        return AgentOutputEnvelope(
            metadata={
                "field_group": field_group,
                "decision": decision,
                "valid": decision != "invalid",
                "auto_apply": decision == "auto_apply",
                "review_count": len(review_items),
                "error_count": len(issues),
                "confidence_threshold": threshold,
                "min_confidence": _min_confidence(fields),
                "conflict_count": sum(1 for field in fields if field.get("decision") == "conflict_review"),
            },
            state={
                "field_group": field_group,
                "decision": decision,
                "normalized_record": output_record,
                "write_payload": write_payload,
                "review_queue": [_model_json(item) for item in review_items],
                "validation_errors": [_model_json(issue) for issue in issues],
            },
            turns=1,
            tokens=0,
            usd=Decimal("0"),
        )


def _record_from_params(params: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    for key in ("normalized_record", "record", "payload"):
        value = params.get(key)
        if isinstance(value, Mapping):
            return _clean_record(value)
    fields = _reconciled_fields(params)
    record = {
        str(field["field_name"]): field.get("value")
        for field in fields
        if isinstance(field.get("field_name"), str)
    }
    if fields and "confidence" not in record:
        record["confidence"] = _min_confidence(fields)
    return _clean_record(record)


def _raw_input_issues(params: Mapping[str, JsonValue]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for key in ("normalized_record", "record", "payload"):
        value = params.get(key)
        if isinstance(value, Mapping):
            issues.extend(_raw_mapping_issues(value))
    return issues


def _raw_mapping_issues(record: Mapping[str, object]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for key, value in sorted(record.items()):
        field = str(key)
        if _is_raw_key(field) or _looks_like_raw_value(_json_or_none(value)):
            issues.append(ValidationIssue(field=field, reason="raw_source_content"))
    return issues


def _clean_record(record: Mapping[str, object]) -> dict[str, JsonValue]:
    cleaned: dict[str, JsonValue] = {}
    for key, value in record.items():
        field = str(key)
        if _is_raw_key(field) or _looks_like_raw_value(_json_or_none(value)):
            continue
        cleaned[field] = _json_or_none(value)
    return cleaned


def _reconciled_fields(params: Mapping[str, JsonValue]) -> tuple[dict[str, JsonValue], ...]:
    value = params.get("reconciled_fields")
    if not isinstance(value, list):
        return ()
    fields: list[dict[str, JsonValue]] = []
    for item in value:
        if isinstance(item, Mapping):
            cleaned = _clean_record(item)
            if isinstance(cleaned.get("field_name"), str):
                fields.append(cleaned)
    return tuple(fields)


def _review_items(
    fields: tuple[dict[str, JsonValue], ...],
    *,
    record: Mapping[str, JsonValue],
    current: Mapping[str, JsonValue],
    policy: ConfidencePolicy,
) -> tuple[ReviewQueueItem, ...]:
    items: list[ReviewQueueItem] = []
    for field in fields:
        name = field.get("field_name")
        if not isinstance(name, str):
            continue
        confidence = _confidence(field)
        decision = field.get("decision")
        if policy.route(confidence, has_conflict=decision == "conflict_review") == "needs_review":
            items.append(
                ReviewQueueItem(
                    field=name,
                    proposed_value=field.get("value"),
                    current_value=current.get(name),
                    confidence=confidence,
                    reason="conflicting_values" if decision == "conflict_review" else "low_confidence",
                )
            )
    if not fields:
        record_confidence = _record_confidence(record)
        if record_confidence is not None and policy.route(record_confidence) == "needs_review":
            for field_key, value in sorted(record.items()):
                if field_key == "confidence":
                    continue
                items.append(
                    ReviewQueueItem(
                        field=field_key,
                        proposed_value=value,
                        current_value=current.get(field_key),
                        confidence=record_confidence,
                        reason="low_confidence",
                    )
                )
    return tuple(sorted(items, key=lambda item: (item.field, item.reason)))


def _business_rule_issues(record: Mapping[str, JsonValue]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for field, value in sorted(record.items()):
        if value is None:
            continue
        lowered = field.lower()
        if "swift" in lowered and isinstance(value, str) and not _SWIFT_RE.fullmatch(value):
            issues.append(ValidationIssue(field=field, reason="invalid_swift_code"))
        if lowered in {"country_code", "iso_country", "iso_country_code", "country_iso2"}:
            if not isinstance(value, str) or not _ISO2_RE.fullmatch(value):
                issues.append(ValidationIssue(field=field, reason="invalid_iso_country_code"))
        if lowered in {"country", "location_sort_id"}:
            if not isinstance(value, str) or not _COUNTRY_NAME_RE.fullmatch(value):
                issues.append(ValidationIssue(field=field, reason="invalid_country_name"))
    return issues


def _schema_issues(field_group: str, record: Mapping[str, JsonValue]) -> list[ValidationIssue]:
    try:
        schema = schema_for_field_group(field_group)
    except ExtractionConfigError:
        return [ValidationIssue(field="field_group", reason="unsupported_field_group")]
    try:
        schema.model_validate(record)
    except ValidationError as exc:
        return [
            ValidationIssue(field=".".join(str(part) for part in error["loc"]), reason=str(error["type"]))
            for error in exc.errors()
        ]
    return []


def _mapping_param(value: JsonValue | None) -> Mapping[str, JsonValue]:
    return value if isinstance(value, Mapping) else {}


def _threshold(value: JsonValue | None, *, default: float) -> float:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return max(0.0, min(1.0, float(value)))
    return default


def _confidence(field: Mapping[str, JsonValue]) -> float:
    value = field.get("confidence")
    if isinstance(value, int | float) and not isinstance(value, bool):
        return max(0.0, min(1.0, float(value)))
    return 0.0


def _record_confidence(record: Mapping[str, JsonValue]) -> float | None:
    value = record.get("confidence")
    if isinstance(value, int | float) and not isinstance(value, bool):
        return max(0.0, min(1.0, float(value)))
    return None


def _min_confidence(fields: tuple[dict[str, JsonValue], ...]) -> float:
    if not fields:
        return 0.0
    return round(min(_confidence(field) for field in fields), 6)


def _model_json(model: BaseModel) -> dict[str, JsonValue]:
    dumped = model.model_dump(mode="json")
    return dumped if isinstance(dumped, dict) else {}


def _string_param(params: Mapping[str, JsonValue], key: str) -> str:
    value = params.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"ValidationAgent requires string param {key!r}")
    return value


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
