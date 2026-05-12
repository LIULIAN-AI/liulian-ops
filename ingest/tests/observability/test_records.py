from __future__ import annotations

import json

from crawler.observability import InMemoryObservabilitySink


def test_observability_records_redact_raw_content_without_external_services() -> None:
    sink = InMemoryObservabilitySink()

    trace = sink.record_trace(
        "crawl.step",
        attributes={
            "company_id": "company-1",
            "raw_html": "<html>secret-token</html>",
            "nested": {"page_text": "leak-me"},
        },
    )
    metric = sink.record_metric(
        "crawl.tokens",
        12,
        attributes={"raw_document": "secret-token", "status": "ok"},
    )
    event = sink.record_langfuse_event(
        "agent.output",
        trace_id=trace.trace_id,
        input={"raw_text": "secret-token"},
        output={"metadata": {"content": "leak-me"}},
        metadata={"safe": "value"},
    )

    rendered = json.dumps(
        {
            "trace": trace.model_dump(mode="json"),
            "metric": metric.model_dump(mode="json"),
            "event": event.model_dump(mode="json"),
        },
        sort_keys=True,
    )

    assert "secret-token" not in rendered
    assert "leak-me" not in rendered
    assert "[redacted]" in rendered
    assert sink.traces == (trace,)
    assert sink.metrics == (metric,)
    assert sink.langfuse_events == (event,)


def test_langfuse_event_ids_are_unique_for_distinct_payloads() -> None:
    sink = InMemoryObservabilitySink()

    first = sink.record_langfuse_event("agent.output", input={"value": "one"})
    second = sink.record_langfuse_event("agent.output", input={"value": "two"})

    assert first.event_id != second.event_id
