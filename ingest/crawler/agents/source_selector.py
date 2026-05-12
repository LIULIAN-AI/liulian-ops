from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal
from importlib.resources import files
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from crawler.agents.base import AgentConfig, AgentInputEnvelope, AgentOutputEnvelope, SubAgent, ToolContext
from crawler.agents.source_rendering import SourceConfigError, render_params, render_url_template
from crawler.harness.checkpoint import JsonValue

CostTier = Literal["low", "medium", "high"]
_COST_ORDER: dict[CostTier, int] = {"low": 0, "medium": 1, "high": 2}
_DEFAULT_CONFIG_DIR = Path(__file__).resolve().parents[3] / "config"
_RAW_PARAM_MARKERS = ("html", "body", "content", "document", "markdown", "raw", "text")

__all__ = (
    "DataDictionary",
    "FieldDefinition",
    "SourceConfigError",
    "SourceDefinition",
    "SourceRegistry",
    "SourceSelectorAgent",
    "load_data_dictionary",
    "load_source_registry",
    "render_url_template",
)


class FieldDefinition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    type: str = Field(default="string", min_length=1)
    description: str = Field(default="", min_length=0)


class DataDictionary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    field_groups: dict[str, tuple[FieldDefinition, ...]] = Field(default_factory=dict)

    @field_validator("field_groups", mode="before")
    @classmethod
    def _coerce_field_groups(cls, value: object) -> object:
        if isinstance(value, Mapping):
            return {
                str(group): [FieldDefinition.model_validate(item) for item in _as_sequence(items)]
                for group, items in value.items()
            }
        return value

    def known_groups_for(self, field_group: str) -> tuple[str, ...]:
        return tuple(
            group for group in self.field_groups if _group_matches(field_group, tuple([group]))
        )


class SourceDefinition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1)
    source_type: str = Field(min_length=1)
    adapter: str = Field(min_length=1)
    tool: str = Field(min_length=1)
    cadence: str = Field(default="manual", min_length=1)
    cost_tier: CostTier = "low"
    field_groups: tuple[str, ...] = Field(default_factory=tuple)
    url_template: str | None = None
    params: dict[str, JsonValue] = Field(default_factory=dict)
    enabled: bool = True

    @field_validator("field_groups", mode="before")
    @classmethod
    def _coerce_field_groups(cls, value: object) -> object:
        if value is None:
            return ()
        if isinstance(value, str):
            return (value,)
        return value

    @model_validator(mode="after")
    def _require_adapter_or_tool(self) -> SourceDefinition:
        if not self.adapter.strip() or not self.tool.strip():
            raise ValueError("SourceDefinition requires non-empty adapter and tool")
        return self


class SourceRegistry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    sources: tuple[SourceDefinition, ...] = Field(default_factory=tuple)

    @field_validator("sources", mode="before")
    @classmethod
    def _coerce_sources(cls, value: object) -> object:
        if isinstance(value, Mapping):
            return [dict({"id": key}, **cast(Mapping[str, object], item)) for key, item in value.items()]
        return value

    def get(self, source_id: str) -> SourceDefinition:
        for source in self.sources:
            if source.id == source_id:
                return source
        raise KeyError(source_id)

    def candidates(
        self,
        *,
        field_group: str,
        company: Mapping[str, JsonValue],
        source_type: str | None = None,
        source_id: str | None = None,
        max_cost_tier: CostTier | None = None,
    ) -> tuple[dict[str, JsonValue], ...]:
        selected = tuple(
            (index, source)
            for index, source in enumerate(self.sources)
            if _source_matches(
                source,
                field_group=field_group,
                source_type=source_type,
                source_id=source_id,
                max_cost_tier=max_cost_tier,
            )
        )
        ordered = selected if source_id else tuple(sorted(selected, key=_source_sort_key))
        return tuple(
            _candidate_json(source, company={**company, "field_group": field_group})
            for _index, source in ordered
        )


class SourceSelectorAgent(SubAgent):
    config = AgentConfig(name="source_selector", role="declarative source selection")

    def __init__(
        self,
        *,
        registry_config: SourceRegistry | None = None,
        data_dictionary: DataDictionary | None = None,
        config_dir: Path | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        resolved_dir = config_dir or _DEFAULT_CONFIG_DIR
        self._registry = registry_config or load_source_registry(resolved_dir / "sources.yaml")
        self._dictionary = data_dictionary or load_data_dictionary(
            resolved_dir / "data_dictionary.yaml"
        )

    async def execute(
        self,
        envelope: AgentInputEnvelope,
        context: ToolContext,
    ) -> AgentOutputEnvelope:
        _ = context
        field_group = _string_param(envelope.params, "field_group")
        source_id = _optional_string_param(envelope.params, "source_id") or _optional_string_param(
            envelope.params, "source"
        )
        candidates = self._registry.candidates(
            field_group=field_group,
            company=_company_params(envelope.params),
            source_type=_optional_string_param(envelope.params, "source_type"),
            source_id=source_id,
            max_cost_tier=_optional_cost_tier(envelope.params),
        )
        return AgentOutputEnvelope(
            metadata={
                "field_group": field_group,
                "candidate_count": len(candidates),
                "known_field_groups": list(self._dictionary.known_groups_for(field_group)),
                "requested_source_id": source_id,
            },
            state={"source_candidates": list(candidates)},
            turns=1,
            tokens=0,
            usd=Decimal("0"),
        )


def load_source_registry(path: Path | str = _DEFAULT_CONFIG_DIR / "sources.yaml") -> SourceRegistry:
    return SourceRegistry.model_validate(_load_yaml_mapping(_resolve_config_path(path)))


def load_data_dictionary(path: Path | str = _DEFAULT_CONFIG_DIR / "data_dictionary.yaml") -> DataDictionary:
    return DataDictionary.model_validate(_load_yaml_mapping(_resolve_config_path(path)))


def _source_matches(
    source: SourceDefinition,
    *,
    field_group: str,
    source_type: str | None,
    source_id: str | None,
    max_cost_tier: CostTier | None,
) -> bool:
    if not source.enabled:
        return False
    if source_id is not None:
        return source.id == source_id
    if source_type is not None and source.source_type != source_type:
        return False
    if max_cost_tier is not None and _COST_ORDER[source.cost_tier] > _COST_ORDER[max_cost_tier]:
        return False
    return _group_matches(field_group, source.field_groups)


def _group_matches(field_group: str, source_groups: Sequence[str]) -> bool:
    for source_group in source_groups:
        if source_group == "*" or source_group == field_group:
            return True
        if field_group.startswith(f"{source_group}."):
            return True
    return False


def _source_sort_key(indexed_source: tuple[int, SourceDefinition]) -> tuple[int, int]:
    index, source = indexed_source
    return (_COST_ORDER[source.cost_tier], index)


def _candidate_json(source: SourceDefinition, *, company: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    rendered_url, missing = render_url_template(source.url_template, company)
    candidate: dict[str, JsonValue] = {
        "source_id": source.id,
        "source_type": source.source_type,
        "adapter": source.adapter,
        "tool": source.tool,
        "cadence": source.cadence,
        "cost_tier": source.cost_tier,
        "url_template": source.url_template,
        "rendered_params": dict(source.params),
    }
    rendered_params = render_params(source.params, company)
    candidate["rendered_params"] = rendered_params
    if rendered_url is not None:
        candidate["rendered_params"] = {**rendered_params, "url": rendered_url}
    if missing:
        candidate["missing_template_variables"] = list(missing)
    return candidate


def _company_params(params: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    company: dict[str, JsonValue] = {}
    nested = params.get("company")
    if isinstance(nested, Mapping):
        company.update(
            {
                str(key): value
                for key, value in nested.items()
                if not _looks_like_raw_param(str(key), value)
            }
        )
    for key, value in params.items():
        if (
            key not in {"company", "source_type", "source_id", "source", "max_cost_tier"}
            and not _looks_like_raw_param(key, value)
        ):
            company[key] = value
    if "company_sort_id" not in company and "sort_id" in company:
        company["company_sort_id"] = company["sort_id"]
    if "sort_id" not in company and "company_sort_id" in company:
        company["sort_id"] = company["company_sort_id"]
    return company


def _looks_like_raw_param(key: str, value: JsonValue) -> bool:
    lowered = key.lower()
    return isinstance(value, str) and any(marker in lowered for marker in _RAW_PARAM_MARKERS)


def _string_param(params: Mapping[str, JsonValue], key: str) -> str:
    value = params.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"SourceSelectorAgent requires string param {key!r}")
    return value


def _optional_string_param(params: Mapping[str, JsonValue], key: str) -> str | None:
    value = params.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError(f"SourceSelectorAgent requires string param {key!r}")
    return value


def _optional_cost_tier(params: Mapping[str, JsonValue]) -> CostTier | None:
    value = _optional_string_param(params, "max_cost_tier")
    if value is None:
        return None
    if value not in _COST_ORDER:
        raise ValueError(f"Unsupported max_cost_tier: {value!r}")
    return value


def _load_yaml_mapping(path: Path) -> dict[str, object]:
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore[import-untyped]
    except ModuleNotFoundError:
        loaded = _parse_simple_yaml(text)
    else:
        loaded = yaml.safe_load(text)
    if not isinstance(loaded, dict):
        raise SourceConfigError(f"Expected mapping in {path}")
    return cast(dict[str, object], loaded)


def _resolve_config_path(path: Path | str) -> Path:
    candidate = Path(path)
    if candidate.exists():
        return candidate
    packaged_name = candidate.name
    resource = files("crawler.config").joinpath(packaged_name)
    return Path(str(resource))


def _parse_simple_yaml(text: str) -> object:
    from json import JSONDecodeError, loads

    try:
        return loads(text)
    except JSONDecodeError as exc:
        raise SourceConfigError(
            "PyYAML is not installed; fallback parser expects JSON-compatible YAML"
        ) from exc


def _as_sequence(value: object) -> Sequence[object]:
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return value
    raise TypeError("Expected a sequence")
