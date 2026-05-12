from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal, cast
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, ConfigDict, Field

from crawler.harness.checkpoint import JsonValue
from crawler.harness.distill import redact_json
from crawler.observability.records import InMemoryObservabilitySink, LangfuseEventRecord, MetricRecord

ConfidenceRoute = Literal["auto_apply", "needs_review", "invalid"]
ReviewStatus = Literal["pending", "approved", "rejected", "skipped"]
_TERMINAL_REVIEW_STATUSES = frozenset({"approved", "rejected", "skipped"})


class ConfidencePolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    auto_apply_threshold: float = Field(default=0.75, ge=0.0, le=1.0)
    invalid_threshold: float = Field(default=0.25, ge=0.0, le=1.0)

    def route(self, confidence: float | int | None, *, has_conflict: bool = False, has_error: bool = False) -> ConfidenceRoute:
        value = _confidence(confidence)
        if has_error or value < self.invalid_threshold:
            return "invalid"
        if has_conflict or value < self.auto_apply_threshold:
            return "needs_review"
        return "auto_apply"


class ReviewQueueItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    item_id: str
    field: str
    proposed_value: JsonValue
    current_value: JsonValue = None
    confidence: float = Field(..., ge=0.0, le=1.0)
    reason: str
    status: ReviewStatus = "pending"
    reviewer: str | None = None
    decision_reason: str | None = None
    created_at: datetime
    decided_at: datetime | None = None


class CreateReviewQueueItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    field: str = Field(min_length=1)
    proposed_value: JsonValue
    current_value: JsonValue = None
    confidence: float = Field(..., ge=0.0, le=1.0)
    reason: str = Field(min_length=1, max_length=200)
    item_id: str | None = Field(default=None, min_length=1)


class InMemoryReviewQueueStore:
    def __init__(self) -> None:
        self._items: dict[str, ReviewQueueItem] = {}

    def create(self, request: CreateReviewQueueItem, *, at: datetime | None = None) -> ReviewQueueItem:
        payload = _sanitize_review_payload(
            {
                "field": request.field,
                "proposed_value": request.proposed_value,
                "current_value": request.current_value,
                "confidence": request.confidence,
                "reason": request.reason,
            }
        )
        item_id = request.item_id or _stable_id("review", payload)
        existing = self._items.get(item_id)
        if existing is not None:
            return existing
        item = ReviewQueueItem(
            item_id=item_id,
            field=request.field,
            proposed_value=payload.get("proposed_value"),
            current_value=payload.get("current_value"),
            confidence=request.confidence,
            reason=request.reason,
            created_at=at or datetime.now(UTC),
        )
        self._items[item.item_id] = item
        return item

    def list(self, *, status: ReviewStatus | None = None, limit: int = 100) -> tuple[ReviewQueueItem, ...]:
        items = sorted(self._items.values(), key=lambda item: (item.created_at, item.item_id))
        if status is not None:
            items = [item for item in items if item.status == status]
        return tuple(items[:limit])

    def decide(
        self,
        item_id: str,
        *,
        decision: Literal["approved", "rejected", "skipped"],
        reason: str | None = None,
        reviewer: str | None = None,
        at: datetime | None = None,
    ) -> ReviewQueueItem | None:
        item = self._items.get(item_id)
        if item is None:
            return None
        if item.status in _TERMINAL_REVIEW_STATUSES:
            return item
        decided = item.model_copy(
            update={
                "status": decision,
                "reviewer": reviewer,
                "decision_reason": reason,
                "decided_at": at or datetime.now(UTC),
            }
        )
        self._items[item_id] = decided
        return decided


class CostSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    total_tokens: int
    total_usd: Decimal
    metric_count: int
    langfuse_event_count: int


class FreshnessSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    latest_observed_at: datetime | None
    observed_records: int
    stale_records: int
    max_age_seconds: int | None


class DashboardSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    cost: CostSummary
    freshness: FreshnessSummary


def summarize_dashboard(
    sink: InMemoryObservabilitySink,
    *,
    now: datetime | None = None,
    stale_after_seconds: int = 86_400,
) -> DashboardSummary:
    return DashboardSummary(
        cost=_cost_summary(sink.metrics, sink.langfuse_events),
        freshness=_freshness_summary(
            (*sink.metrics, *sink.langfuse_events, *sink.traces),
            now=now or datetime.now(UTC),
            stale_after_seconds=stale_after_seconds,
        ),
    )


def _cost_summary(metrics: Iterable[MetricRecord], events: Iterable[LangfuseEventRecord]) -> CostSummary:
    total_tokens = 0
    total_usd = Decimal("0")
    metric_count = 0
    event_count = 0
    for metric in metrics:
        metric_count += 1
        name = metric.name.lower()
        unit = metric.unit.lower()
        if "token" in name or "token" in unit:
            total_tokens += int(metric.value)
        if unit in {"usd", "dollar", "dollars"} or "usd" in name or "cost" in name:
            total_usd += metric.value
    for event in events:
        event_count += 1
        usage = _usage_values(event.input) + _usage_values(event.output) + _usage_values(event.metadata)
        for key, value in usage:
            if "token" in key:
                total_tokens += int(value)
            if key in {"usd", "cost_usd", "usage_usd"} or ("cost" in key and "usd" in key):
                total_usd += value
    return CostSummary(
        total_tokens=total_tokens,
        total_usd=total_usd,
        metric_count=metric_count,
        langfuse_event_count=event_count,
    )


def _freshness_summary(
    records: Iterable[object],
    *,
    now: datetime,
    stale_after_seconds: int,
) -> FreshnessSummary:
    latest: datetime | None = None
    observed = 0
    stale = 0
    max_age: int | None = None
    for record in records:
        occurred_at = getattr(record, "occurred_at", None)
        if not isinstance(occurred_at, datetime):
            continue
        observed += 1
        latest = occurred_at if latest is None else max(latest, occurred_at)
        age = max(0, int((now - occurred_at).total_seconds()))
        max_age = age if max_age is None else max(max_age, age)
        if age > stale_after_seconds:
            stale += 1
    return FreshnessSummary(
        latest_observed_at=latest,
        observed_records=observed,
        stale_records=stale,
        max_age_seconds=max_age,
    )


def _usage_values(value: JsonValue | Mapping[str, JsonValue]) -> list[tuple[str, Decimal]]:
    found: list[tuple[str, Decimal]] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            lowered = str(key).lower()
            if isinstance(child, int | float | str) and not isinstance(child, bool):
                if "token" in lowered or "usd" in lowered or "cost" in lowered:
                    try:
                        found.append((lowered, Decimal(str(child))))
                    except Exception:
                        pass
            if isinstance(child, Mapping):
                found.extend(_usage_values(child))
            if isinstance(child, list):
                for item in child:
                    found.extend(_usage_values(item))
    return found


def _sanitize_review_payload(value: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    return cast(dict[str, JsonValue], _redact_secret_values(redact_json(dict(value))))


def _redact_secret_values(value: JsonValue) -> JsonValue:
    if isinstance(value, Mapping):
        return {
            str(key): "[redacted]" if _is_secret_key(str(key)) else _redact_secret_values(child)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [_redact_secret_values(child) for child in value]
    if isinstance(value, str) and _looks_like_raw_or_secret_value(value):
        return "[redacted]"
    return value


def _is_secret_key(key: str) -> bool:
    compact = "".join(char for char in key.lower() if char.isalnum())
    exact = {
        "apikey",
        "authorization",
        "password",
        "secret",
        "token",
    }
    return compact in exact or compact.endswith(("apikey", "password", "secret", "token"))


def _looks_like_raw_or_secret_value(value: str) -> bool:
    lowered = value.lower()
    if "<html" in lowered or "<script" in lowered or "</body" in lowered or "<div" in lowered:
        return True
    return len(value) > 800


def _confidence(value: float | int | None) -> float:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return max(0.0, min(1.0, float(value)))
    return 0.0


def _stable_id(kind: str, payload: Mapping[str, JsonValue]) -> str:
    from json import dumps

    canonical = dumps(payload, sort_keys=True, separators=(",", ":"))
    return str(uuid5(NAMESPACE_URL, f"neobanker-crawler:{kind}:{canonical}"))
