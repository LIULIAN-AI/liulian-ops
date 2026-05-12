from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from math import sqrt
import re
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from crawler.harness.checkpoint import JsonValue
from crawler.harness.distill import RAW_CONTENT_KEYS


class VectorSearchResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    point_id: str = Field(min_length=1)
    score: float = Field(ge=0.0, le=1.0)
    payload: dict[str, JsonValue] = Field(default_factory=dict)


class VectorIndex(Protocol):
    def upsert(
        self,
        collection: str,
        points: Iterable[Mapping[str, JsonValue]],
    ) -> tuple[str, ...]: ...

    def search(
        self,
        collection: str,
        query: str,
        *,
        limit: int = 5,
        min_score: float = 0.0,
    ) -> tuple[VectorSearchResult, ...]: ...

    def delete(self, collection: str, point_ids: Iterable[str]) -> tuple[str, ...]: ...


class InMemoryQdrantTool:
    """Small offline vector index with a Qdrant-shaped surface for tests."""

    def __init__(self) -> None:
        self._collections: dict[str, dict[str, _StoredPoint]] = {}

    def upsert(
        self,
        collection: str,
        points: Iterable[Mapping[str, JsonValue]],
    ) -> tuple[str, ...]:
        bucket = self._collections.setdefault(collection, {})
        upserted: list[str] = []
        for point in points:
            point_id = _string_field(point, "id") or _string_field(point, "point_id")
            text = _string_field(point, "text") or _payload_text(point)
            if point_id is None or not text:
                continue
            payload = point.get("payload")
            safe_payload = sanitize_payload(payload if isinstance(payload, Mapping) else point)
            bucket[point_id] = _StoredPoint(vector=_embed(text), payload=safe_payload)
            upserted.append(point_id)
        return tuple(sorted(upserted))

    def search(
        self,
        collection: str,
        query: str,
        *,
        limit: int = 5,
        min_score: float = 0.0,
    ) -> tuple[VectorSearchResult, ...]:
        query_vector = _embed(query)
        results = [
            VectorSearchResult(
                point_id=point_id,
                score=_cosine(query_vector, point.vector),
                payload=point.payload,
            )
            for point_id, point in self._collections.get(collection, {}).items()
        ]
        filtered = [item for item in results if item.score >= min_score]
        return tuple(sorted(filtered, key=lambda item: (-item.score, item.point_id))[:limit])

    def delete(self, collection: str, point_ids: Iterable[str]) -> tuple[str, ...]:
        bucket = self._collections.setdefault(collection, {})
        deleted: list[str] = []
        for point_id in point_ids:
            if point_id in bucket:
                del bucket[point_id]
                deleted.append(point_id)
        return tuple(sorted(deleted))


class _StoredPoint(BaseModel):
    model_config = ConfigDict(frozen=True)

    vector: dict[str, float]
    payload: dict[str, JsonValue]


def sanitize_payload(value: Mapping[str, object]) -> dict[str, JsonValue]:
    safe: dict[str, JsonValue] = {}
    for key, child in sorted(value.items()):
        normalized = str(key).lower()
        if _raw_payload_key(normalized):
            continue
        json_child = _json_value(child)
        if json_child is not None or child is None:
            safe[str(key)] = json_child
    return safe


def _raw_payload_key(key: str) -> bool:
    if key in {"content_hash", "source_url"}:
        return False
    return key in RAW_CONTENT_KEYS or key.startswith("raw_") or key.endswith("_content")


def _payload_text(point: Mapping[str, JsonValue]) -> str:
    payload = point.get("payload")
    fields: Mapping[str, JsonValue]
    if isinstance(payload, Mapping):
        fields = payload
    else:
        fields = point
    return " ".join(
        str(value)
        for key, value in sorted(fields.items())
        if isinstance(value, str) and not _raw_payload_key(str(key).lower())
    )


def _string_field(point: Mapping[str, JsonValue], key: str) -> str | None:
    value = point.get(key)
    return value if isinstance(value, str) and value else None


def _json_value(value: object) -> JsonValue:
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, Mapping):
        return sanitize_payload(value)
    return str(value)


def _embed(text: str) -> dict[str, float]:
    counts = Counter(re.findall(r"[a-z0-9]+", text.lower()))
    return {token: float(count) for token, count in counts.items()}


def _cosine(left: Mapping[str, float], right: Mapping[str, float]) -> float:
    if not left or not right:
        return 0.0
    dot = sum(value * right.get(key, 0.0) for key, value in left.items())
    left_norm = sqrt(sum(value * value for value in left.values()))
    right_norm = sqrt(sum(value * value for value in right.values()))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return max(0.0, min(1.0, dot / (left_norm * right_norm)))
