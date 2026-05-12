from __future__ import annotations

from typing import cast

from fastapi.testclient import TestClient

from crawler.agents.source_selector import SourceRegistry
from crawler.api.main import create_app
from crawler.api.run_registry import InMemoryRunRegistry
from crawler.hardening import InMemoryReviewQueueStore
from crawler.harness.checkpoint import JsonValue
from crawler.harness.dispatch import InMemoryStreamProducer, PublishResult, StreamEvent
from crawler.observability.records import InMemoryObservabilitySink


def test_health_uses_in_memory_defaults() -> None:
    client = TestClient(create_app(registry=_registry()))

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "version": "0.1.0",
        "producer": "InMemoryStreamProducer",
        "queued_events": 0,
        "sources": 2,
        "observability_events": 0,
    }


def test_admin_trigger_queues_redacted_workflow_request() -> None:
    producer = InMemoryStreamProducer(stream="crawler:workflow")
    sink = InMemoryObservabilitySink()
    client = TestClient(create_app(producer=producer, registry=_registry(), observability_sink=sink))

    response = client.post(
        "/admin/runs/trigger?workflow=refresh_field_group",
        json={
            "requested_by": "unit-test",
            "params": {
                "bank_id": "bank-1",
                "field_group": "marketing",
                "raw_html": "<html>secret</html>",
            },
        },
    )

    assert response.status_code == 200
    body = response.json()["run"]
    assert body["workflow"] == "refresh_field_group"
    assert body["status"] == "queued"
    assert body["stream"] == "crawler:workflow"
    assert body["stream_id"] == "memory-1"
    assert body["params"] == {
        "bank_id": "bank-1",
        "field_group": "marketing",
        "raw_html": "[redacted]",
    }
    assert "<html>secret</html>" not in response.text
    event_params = cast(dict[str, JsonValue], producer.events[0].payload["params"])
    assert event_params["raw_html"] == "[redacted]"
    assert sink.langfuse_events[0].input == {}


def test_list_runs_returns_sanitized_shape_without_request_content() -> None:
    producer = InMemoryStreamProducer(stream="crawler:workflow")
    client = TestClient(create_app(producer=producer, registry=_registry()))
    client.post(
        "/admin/runs/trigger?workflow=refresh_company",
        json={"params": {"company_id": "company-1", "raw_text": "do not echo"}},
    )

    response = client.get("/admin/runs")

    assert response.status_code == 200
    runs = response.json()["runs"]
    assert len(runs) == 1
    assert runs[0]["workflow"] == "refresh_company"
    assert runs[0]["params"]["raw_text"] == "[redacted]"
    assert "do not echo" not in response.text


def test_sources_are_listed_from_registry_config() -> None:
    client = TestClient(create_app(registry=_registry()))

    response = client.get("/sources")
    source_response = client.get("/sources/official_site")

    assert response.status_code == 200
    assert [source["id"] for source in response.json()["sources"]] == ["official_site", "search"]
    assert source_response.status_code == 200
    assert source_response.json()["field_groups"] == ["about", "marketing"]
    assert client.get("/sources/missing").status_code == 404


def test_review_queue_endpoints_store_create_list_and_decide_real_items() -> None:
    store = InMemoryReviewQueueStore()
    client = TestClient(create_app(registry=_registry(), review_queue_store=store))

    create_response = client.post(
        "/review/queue",
        json={
            "field": "description",
            "proposed_value": {"content": "do not echo", "summary": "Digital bank"},
            "confidence": 0.62,
            "reason": "low_confidence",
        },
    )

    queue_response = client.get("/review/queue")
    item_id = create_response.json()["item_id"]
    decision_response = client.post(
        f"/review/queue/{item_id}/decision",
        json={"decision": "skipped", "reason": "not ready", "reviewer": "unit-test"},
    )
    pending_response = client.get("/review/queue?status=pending")

    assert create_response.status_code == 200
    assert create_response.json()["field"] == "description"
    assert create_response.json()["proposed_value"] == {"content": "[redacted]", "summary": "Digital bank"}
    assert queue_response.status_code == 200
    assert len(queue_response.json()["items"]) == 1
    assert decision_response.status_code == 200
    assert decision_response.json()["status"] == "skipped"
    assert decision_response.json()["decision_reason"] == "not ready"
    assert decision_response.json()["reviewer"] == "unit-test"
    assert pending_response.json() == {"items": []}
    assert "do not echo" not in create_response.text


def test_admin_dashboard_returns_cost_and_freshness_shape() -> None:
    sink = InMemoryObservabilitySink()
    sink.record_metric("crawl.tokens", 12, unit="tokens")
    sink.record_metric("crawl.cost_usd", "0.03", unit="usd")
    sink.record_langfuse_event(
        "agent.output",
        output={"usage": {"total_tokens": 8, "cost_usd": "0.02"}},
    )
    client = TestClient(create_app(registry=_registry(), observability_sink=sink))

    response = client.get("/admin/dashboard")

    assert response.status_code == 200
    assert response.json()["cost"] == {
        "total_tokens": 20,
        "total_usd": "0.05",
        "metric_count": 2,
        "langfuse_event_count": 1,
    }
    assert response.json()["freshness"]["observed_records"] == 3
    assert set(response.json()) == {"cost", "freshness"}


def test_runs_use_registry_not_producer_private_state() -> None:
    class PublishOnlyProducer:
        def __init__(self) -> None:
            self.calls: list[StreamEvent] = []

        async def publish(self, event: StreamEvent) -> PublishResult:
            self.calls.append(event)
            return PublishResult(
                stream="redis:workflow",
                stream_id="1-0",
                dedup_key=event.dedup_key,
            )

    producer = PublishOnlyProducer()
    registry = InMemoryRunRegistry()
    client = TestClient(
        create_app(
            producer=producer,  # type: ignore[arg-type]
            registry=_registry(),
            run_registry=registry,
        )
    )

    trigger_response = client.post(
        "/admin/runs/trigger?workflow=refresh_company",
        json={"params": {"company_id": "company-1"}},
    )
    list_response = client.get("/admin/runs")

    assert trigger_response.status_code == 200
    assert trigger_response.json()["run"]["stream"] == "redis:workflow"
    assert list_response.status_code == 200
    assert list_response.json()["runs"][0]["stream_id"] == "1-0"
    assert len(producer.calls) == 1


def test_health_accepts_publish_only_producer() -> None:
    class PublishOnlyProducer:
        async def publish(self, event: StreamEvent) -> PublishResult:
            return PublishResult(stream="redis:workflow", stream_id="1-0", dedup_key=event.dedup_key)

    client = TestClient(create_app(producer=PublishOnlyProducer(), registry=_registry()))  # type: ignore[arg-type]

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["queued_events"] is None


def _registry() -> SourceRegistry:
    return SourceRegistry.model_validate(
        {
            "sources": [
                {
                    "id": "official_site",
                    "source_type": "http",
                    "adapter": "official_site",
                    "tool": "fetch.http",
                    "cadence": "weekly",
                    "cost_tier": "low",
                    "field_groups": ["about", "marketing"],
                    "url_template": "https://{domain}",
                },
                {
                    "id": "search",
                    "source_type": "search",
                    "adapter": "search",
                    "tool": "fetch.search",
                    "cadence": "manual",
                    "cost_tier": "medium",
                    "field_groups": ["*"],
                },
            ]
        }
    )
