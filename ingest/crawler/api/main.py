from __future__ import annotations

from fastapi import FastAPI

from crawler.agents.source_selector import SourceRegistry, load_source_registry
from crawler.api.run_registry import InMemoryRunRegistry
from crawler.api.routers import dashboard, health, review, runs, sources
from crawler.hardening import InMemoryReviewQueueStore
from crawler.harness.dispatch import InMemoryStreamProducer
from crawler.observability.records import InMemoryObservabilitySink


def create_app(
    *,
    producer: InMemoryStreamProducer | None = None,
    registry: SourceRegistry | None = None,
    observability_sink: InMemoryObservabilitySink | None = None,
    run_registry: InMemoryRunRegistry | None = None,
    review_queue_store: InMemoryReviewQueueStore | None = None,
) -> FastAPI:
    app = FastAPI(title="Neobanker Crawler Control Plane", version="0.1.0")
    app.state.producer = producer or InMemoryStreamProducer(stream="crawler:workflow")
    app.state.registry = registry or load_source_registry()
    app.state.observability_sink = observability_sink or InMemoryObservabilitySink()
    app.state.run_registry = run_registry or InMemoryRunRegistry()
    app.state.review_queue_store = review_queue_store or InMemoryReviewQueueStore()

    app.include_router(dashboard.router)
    app.include_router(health.router)
    app.include_router(runs.router)
    app.include_router(sources.router)
    app.include_router(review.router)
    return app


app = create_app()
