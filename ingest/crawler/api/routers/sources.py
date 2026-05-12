from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from crawler.agents.source_selector import CostTier
from crawler.api.dependencies import RegistryDep

router = APIRouter(prefix="/sources", tags=["sources"])


class SourceResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    source_type: str
    adapter: str
    tool: str
    cadence: str
    cost_tier: CostTier
    field_groups: tuple[str, ...]
    enabled: bool


class ListSourcesResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    sources: tuple[SourceResponse, ...]


@router.get("", response_model=ListSourcesResponse)
async def list_sources(registry: RegistryDep) -> ListSourcesResponse:
    return ListSourcesResponse(
        sources=tuple(SourceResponse.model_validate(source.model_dump()) for source in registry.sources)
    )


@router.get("/{source_id}", response_model=SourceResponse)
async def get_source(source_id: str, registry: RegistryDep) -> SourceResponse:
    try:
        source = registry.get(source_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Source not found") from exc
    return SourceResponse.model_validate(source.model_dump())

