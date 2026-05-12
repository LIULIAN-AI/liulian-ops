from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal
from difflib import SequenceMatcher
import re
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field

from crawler.agents.base import AgentConfig, AgentInputEnvelope, AgentOutputEnvelope, SubAgent, ToolContext
from crawler.harness.checkpoint import JsonValue
from crawler.tools.qdrant import VectorIndex
from crawler.utils.guards import is_raw_key


WIKIPEDIA_SEEDS: tuple[dict[str, JsonValue], ...] = (
    {
        "name": "List of neobanks",
        "source_type": "wikipedia",
        "url": "https://en.wikipedia.org/wiki/Neobank",
        "query": "neobank digital bank challenger bank",
    },
    {
        "name": "List of online banks",
        "source_type": "wikipedia",
        "url": "https://en.wikipedia.org/wiki/Online_banking",
        "query": "online bank mobile banking app fintech",
    },
)
REGULATOR_SEEDS: tuple[dict[str, JsonValue], ...] = (
    {
        "name": "OCC national bank list",
        "source_type": "regulator",
        "url": "https://www.occ.treas.gov/topics/charters-and-licensing/",
        "query": "digital national bank fintech charter",
    },
    {
        "name": "FDIC BankFind suite",
        "source_type": "regulator",
        "url": "https://banks.data.fdic.gov/bankfind-suite/",
        "query": "internet bank mobile bank digital banking",
    },
)

_POSITIVE_MARKERS = (
    "neobank",
    "challenger bank",
    "digital bank",
    "mobile bank",
    "online bank",
    "banking app",
    "fintech bank",
    "branchless",
)
_NEGATIVE_MARKERS = ("not a bank", "traditional bank", "investment bank")


class DiscoveryCandidate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    domain: str | None = None
    country: str | None = None
    summary: str = ""
    source_name: str = Field(default="input", min_length=1)
    source_type: str = Field(default="manual", min_length=1)
    source_url: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class DiscoveryAgent(SubAgent):
    config = AgentConfig(name="discovery", role="neobank candidate discovery")

    def __init__(
        self,
        *,
        similarity_index: VectorIndex | None = None,
        collection: str = "companies",
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._similarity_index = similarity_index
        self._collection = collection

    async def execute(
        self,
        envelope: AgentInputEnvelope,
        context: ToolContext,
    ) -> AgentOutputEnvelope:
        _ = context
        existing = tuple(_clean_mapping(item) for item in _mapping_list(envelope.params, "existing_companies"))
        candidates = _candidate_inputs(envelope.params)
        pending = [
            _pending_record(candidate)
            for candidate in _dedup_candidates(
                candidates,
                existing=existing,
                similarity_index=self._similarity_index,
                collection=_string_param(envelope.params, "collection", self._collection),
                threshold=_float_param(envelope.params, "similarity_threshold", 0.92),
            )
        ]
        return AgentOutputEnvelope(
            metadata={
                "candidate_count": len(candidates),
                "pending_create_count": len(pending),
                "seed_count": len(WIKIPEDIA_SEEDS) + len(REGULATOR_SEEDS),
            },
            state=_state(pending, include_seeds=_bool_param(envelope.params, "include_seeds")),
            turns=1,
            tokens=0,
            usd=Decimal("0"),
        )


def _candidate_inputs(params: Mapping[str, JsonValue]) -> tuple[DiscoveryCandidate, ...]:
    raw = [*_mapping_list(params, "candidates"), *_mapping_list(params, "search_results")]
    normalized = [_normalize_candidate(item) for item in raw]
    filtered = [item for item in normalized if item is not None and _likely_neobank(item)]
    return tuple(sorted(filtered, key=lambda item: (_name_key(item.name), item.domain or "", item.source_url or "")))


def _normalize_candidate(raw: Mapping[str, JsonValue]) -> DiscoveryCandidate | None:
    cleaned = _clean_mapping(raw)
    name = _first_string(cleaned, ("name", "company_name", "title", "label"))
    if name is None:
        return None
    summary = _first_string(cleaned, ("summary", "description", "snippet", "query")) or ""
    return DiscoveryCandidate(
        name=_clean_name(name),
        domain=_clean_domain(_first_string(cleaned, ("domain", "website", "url"))),
        country=_first_string(cleaned, ("country", "jurisdiction")),
        summary=summary.strip(),
        source_name=_safe_short_string(_first_string(cleaned, ("source_name", "source", "name"))) or "input",
        source_type=_first_string(cleaned, ("source_type", "kind")) or "manual",
        source_url=_first_string(cleaned, ("source_url", "url")),
        confidence=_classification_confidence(name, summary),
    )


def _dedup_candidates(
    candidates: Sequence[DiscoveryCandidate],
    *,
    existing: Sequence[Mapping[str, JsonValue]],
    similarity_index: VectorIndex | None,
    collection: str,
    threshold: float,
) -> tuple[DiscoveryCandidate, ...]:
    seen: set[tuple[str, str]] = set()
    kept: list[DiscoveryCandidate] = []
    for candidate in candidates:
        keys = _dedup_keys(candidate)
        if keys & seen or _matches_kept_candidate(candidate, kept):
            continue
        seen.update(keys)
        if _matches_existing(candidate, existing):
            continue
        if _matches_similarity(candidate, similarity_index, collection, threshold):
            continue
        kept.append(candidate)
    return tuple(kept)


def _matches_kept_candidate(
    candidate: DiscoveryCandidate,
    kept: Sequence[DiscoveryCandidate],
) -> bool:
    candidate_name = _name_key(candidate.name)
    for existing in kept:
        if candidate.domain and existing.domain == candidate.domain:
            return True
        existing_name = _name_key(existing.name)
        if existing_name == candidate_name:
            return True
        if existing_name and SequenceMatcher(None, existing_name, candidate_name).ratio() >= 0.9:
            return True
    return False


def _matches_existing(candidate: DiscoveryCandidate, existing: Sequence[Mapping[str, JsonValue]]) -> bool:
    candidate_name = _name_key(candidate.name)
    for company in existing:
        existing_name = _name_key(_first_string(company, ("name", "company_name", "title")) or "")
        existing_domain = _clean_domain(_first_string(company, ("domain", "website", "url")))
        if candidate.domain and existing_domain == candidate.domain:
            return True
        if existing_name == candidate_name:
            return True
        if existing_name and SequenceMatcher(None, existing_name, candidate_name).ratio() >= 0.9:
            return True
    return False


def _dedup_keys(candidate: DiscoveryCandidate) -> set[tuple[str, str]]:
    keys = {("name", _name_key(candidate.name))}
    if candidate.domain:
        keys.add(("domain", candidate.domain))
    return {key for key in keys if key[1]}


def _matches_similarity(
    candidate: DiscoveryCandidate,
    index: VectorIndex | None,
    collection: str,
    threshold: float,
) -> bool:
    if index is None:
        return False
    query = " ".join((candidate.name, candidate.domain or "", candidate.summary))
    return bool(index.search(collection, query, limit=1, min_score=threshold))


def _pending_record(candidate: DiscoveryCandidate) -> dict[str, JsonValue]:
    record: dict[str, JsonValue] = {"company_name": candidate.name, "confidence": candidate.confidence}
    if candidate.domain:
        record["domain"] = candidate.domain
    if candidate.country:
        record["country"] = candidate.country
    provenance: dict[str, JsonValue] = {
        "source_name": candidate.source_name,
        "source_type": candidate.source_type,
    }
    if candidate.source_url:
        provenance["source_url"] = candidate.source_url
    return {
        "action": "pending_create",
        "status": "pending",
        "proposed_record": record,
        "provenance": [provenance],
    }


def _state(pending: list[dict[str, JsonValue]], *, include_seeds: bool) -> dict[str, JsonValue]:
    pending_json: list[JsonValue] = list(pending)
    state: dict[str, JsonValue] = {"pending_create": pending_json}
    if include_seeds:
        seeds_json: list[JsonValue] = [*WIKIPEDIA_SEEDS, *REGULATOR_SEEDS]
        state["discovery_seeds"] = seeds_json
    return state


def _likely_neobank(candidate: DiscoveryCandidate) -> bool:
    text = f"{candidate.name} {candidate.summary}".lower()
    return any(marker in text for marker in _POSITIVE_MARKERS) and not any(
        marker in text for marker in _NEGATIVE_MARKERS
    )


def _classification_confidence(name: str, summary: str) -> float:
    text = f"{name} {summary}".lower()
    hits = sum(1 for marker in _POSITIVE_MARKERS if marker in text)
    penalty = sum(1 for marker in _NEGATIVE_MARKERS if marker in text)
    return max(0.0, min(0.99, 0.55 + hits * 0.12 - penalty * 0.25))


def _mapping_list(params: Mapping[str, JsonValue], key: str) -> list[Mapping[str, JsonValue]]:
    value = params.get(key)
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _clean_mapping(value: Mapping[str, object]) -> dict[str, JsonValue]:
    cleaned: dict[str, JsonValue] = {}
    for key, child in value.items():
        if is_raw_key(str(key)):
            continue
        if isinstance(child, Mapping):
            cleaned[str(key)] = _clean_mapping(child)
        elif isinstance(child, list):
            cleaned[str(key)] = [_json_value(item) for item in child]
        elif isinstance(child, str | int | float | bool) or child is None:
            cleaned[str(key)] = child
    return cleaned


def _json_value(value: object) -> JsonValue:
    if isinstance(value, Mapping):
        return _clean_mapping(value)
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    return str(value)


def _first_string(value: Mapping[str, JsonValue], keys: Sequence[str]) -> str | None:
    for key in keys:
        child = value.get(key)
        if isinstance(child, str) and child.strip():
            return child.strip()
    return None


def _clean_name(name: str) -> str:
    return re.sub(r"\s+", " ", name).strip(" -|")


def _clean_domain(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    parsed = urlparse(normalized if "://" in normalized else f"https://{normalized}")
    domain = (parsed.hostname or "").removeprefix("www.")
    return domain or None


def _safe_short_string(value: str | None) -> str | None:
    if value is None:
        return None
    lowered = value.lower()
    if "<html" in lowered or "</body" in lowered or "<script" in lowered or len(value) > 240:
        return None
    return value


def _name_key(name: str) -> str:
    compact = re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()
    return re.sub(r"\b(inc|ltd|limited|bank|app|financial|finance)\b", "", compact).strip()


def _string_param(params: Mapping[str, JsonValue], key: str, default: str) -> str:
    value = params.get(key)
    return value if isinstance(value, str) and value else default


def _float_param(params: Mapping[str, JsonValue], key: str, default: float) -> float:
    value = params.get(key)
    if isinstance(value, int | float) and not isinstance(value, bool):
        return max(0.0, min(1.0, float(value)))
    return default


def _bool_param(params: Mapping[str, JsonValue], key: str) -> bool:
    return params.get(key) is True
