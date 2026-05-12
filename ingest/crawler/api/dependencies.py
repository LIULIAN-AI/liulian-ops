from __future__ import annotations

from typing import Annotated, cast

from fastapi import Depends, Request

from crawler.agents.source_selector import SourceRegistry
from crawler.api.run_registry import InMemoryRunRegistry
from crawler.hardening import InMemoryReviewQueueStore
from crawler.harness.dispatch import InMemoryStreamProducer
from crawler.observability.records import InMemoryObservabilitySink


def get_producer(request: Request) -> InMemoryStreamProducer:
    return cast(InMemoryStreamProducer, request.app.state.producer)


def get_registry(request: Request) -> SourceRegistry:
    return cast(SourceRegistry, request.app.state.registry)


def get_observability_sink(request: Request) -> InMemoryObservabilitySink:
    return cast(InMemoryObservabilitySink, request.app.state.observability_sink)


def get_run_registry(request: Request) -> InMemoryRunRegistry:
    return cast(InMemoryRunRegistry, request.app.state.run_registry)


def get_review_queue_store(request: Request) -> InMemoryReviewQueueStore:
    return cast(InMemoryReviewQueueStore, request.app.state.review_queue_store)


ProducerDep = Annotated[InMemoryStreamProducer, Depends(get_producer)]
RegistryDep = Annotated[SourceRegistry, Depends(get_registry)]
ObservabilityDep = Annotated[InMemoryObservabilitySink, Depends(get_observability_sink)]
RunRegistryDep = Annotated[InMemoryRunRegistry, Depends(get_run_registry)]
ReviewQueueDep = Annotated[InMemoryReviewQueueStore, Depends(get_review_queue_store)]
