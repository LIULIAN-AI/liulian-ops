from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from crawler.api.dependencies import ObservabilityDep, ProducerDep, RegistryDep

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: str
    version: str
    producer: str
    queued_events: int | None
    sources: int
    observability_events: int


@router.get("/health", response_model=HealthResponse)
async def health(
    producer: ProducerDep,
    registry: RegistryDep,
    observability_sink: ObservabilityDep,
) -> HealthResponse:
    return HealthResponse(
        status="ok",
        version="0.1.0",
        producer=type(producer).__name__,
        queued_events=len(producer.events) if hasattr(producer, "events") else None,
        sources=len(registry.sources),
        observability_events=len(observability_sink.langfuse_events),
    )
