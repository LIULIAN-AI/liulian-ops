from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from crawler.agents.source_selector import SourceDefinition, SourceRegistry
from crawler.harness.dispatch import InMemoryStreamProducer
from crawler.harness.workflows import (
    cron_for_cadence,
    default_workflow_schedules,
    schedule_for_source,
    schedules_for_sources,
    trigger_workflow,
    trigger_workflow_async,
    workflow_trigger_event,
)


def test_cron_schedule_definitions_are_deterministic_and_apscheduler_shaped() -> None:
    first = cron_for_cadence("weekly", key="source:official_site", timezone="America/Los_Angeles")
    second = cron_for_cadence("weekly", key="source:official_site", timezone="America/Los_Angeles")

    assert first == second
    assert first is not None
    assert first.apscheduler_kwargs()["trigger"] == "cron"
    assert first.apscheduler_kwargs()["timezone"] == "America/Los_Angeles"
    assert first.day_of_week in {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}
    assert cron_for_cadence("manual", key="source:search") is None


def test_source_and_workflow_schedules_translate_cadence_without_apscheduler() -> None:
    registry = SourceRegistry.model_validate(
        {
            "sources": [
                _source("official_site", cadence="weekly"),
                _source("search", cadence="manual"),
                {**_source("disabled", cadence="daily"), "enabled": False},
            ]
        }
    )

    specs = schedules_for_sources(registry)
    workflow_specs = default_workflow_schedules()

    assert [spec.schedule_id for spec in specs] == [
        "source:official_site:weekly",
        "source:search:manual",
    ]
    assert specs[0].apscheduler_cron is not None
    assert specs[1].apscheduler_cron is None
    assert {spec.target_id: spec.cadence for spec in workflow_specs}[
        "re_extract_changed_snapshots"
    ] == "hourly"


def test_manual_trigger_event_payload_dedup_and_deterministic_id() -> None:
    producer = InMemoryStreamProducer(stream="crawler:workflow")
    at = datetime(2026, 5, 6, 12, tzinfo=UTC)

    first = trigger_workflow(
        producer,
        "refresh_field_group",
        requested_by="unit",
        at=at,
        bank_id="bank-1",
        field_group="marketing",
        raw_html="<secret>",
    )
    second = trigger_workflow(
        producer,
        "refresh_field_group",
        requested_by="unit",
        at=at,
        field_group="marketing",
        bank_id="bank-1",
        raw_html="<secret>",
    )

    event = producer.events[0]
    assert first.stream_id == "memory-1"
    assert second.duplicate is True
    assert event.event_type == "workflow.requested"
    assert event.dedup_key == first.dedup_key
    assert event.event_id == workflow_trigger_event(
        "refresh_field_group",
        requested_by="unit",
        at=at,
        bank_id="bank-1",
        field_group="marketing",
        raw_html="<secret>",
    ).event_id
    assert event.payload["workflow"] == "refresh_field_group"
    assert event.payload["requested_at"] == at.isoformat()
    assert event.payload["params"] == {
        "bank_id": "bank-1",
        "field_group": "marketing",
        "raw_html": "[redacted]",
    }


def test_trigger_dedup_key_includes_workflow_when_checkpoint_key_is_shared() -> None:
    first = workflow_trigger_event("refresh_company", checkpoint_key="daily")
    second = workflow_trigger_event("refresh_field_group", checkpoint_key="daily")

    assert first.dedup_key == "workflow:refresh_company:daily:requested"
    assert second.dedup_key == "workflow:refresh_field_group:daily:requested"
    assert first.event_id != second.event_id


def test_async_trigger_accepts_async_producer_style() -> None:
    class AsyncProducer:
        def __init__(self) -> None:
            self.inner = InMemoryStreamProducer()

        async def publish(self, event):  # type: ignore[no-untyped-def]
            return self.inner.publish(event)

    async def run() -> None:
        result = await trigger_workflow_async(
            AsyncProducer(),
            "refresh_company",
            company_id="company-1",
        )

        assert result.stream_id == "memory-1"

    asyncio.run(run())


def test_schedule_for_source_rejects_unknown_cadence() -> None:
    source = SourceDefinition.model_validate(_source("bad", cadence="fortnightly"))

    try:
        schedule_for_source(source)
    except ValueError as exc:
        assert "Unsupported cadence" in str(exc)
    else:
        raise AssertionError("unknown cadence should fail")


def _source(source_id: str, *, cadence: str) -> dict[str, object]:
    return {
        "id": source_id,
        "source_type": "http",
        "adapter": source_id,
        "tool": "fetch.http",
        "cadence": cadence,
        "cost_tier": "low",
        "field_groups": ["about"],
        "url_template": "https://{domain}",
    }
