from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
import json

from crawler.hardening import ConfidencePolicy, CreateReviewQueueItem, InMemoryReviewQueueStore, summarize_dashboard
from crawler.observability.records import InMemoryObservabilitySink


def test_confidence_policy_threshold_routes_are_deterministic() -> None:
    policy = ConfidencePolicy(auto_apply_threshold=0.8, invalid_threshold=0.3)

    assert policy.route(0.95) == "auto_apply"
    assert policy.route(0.5) == "needs_review"
    assert policy.route(0.2) == "invalid"
    assert policy.route(0.95, has_conflict=True) == "needs_review"
    assert policy.route(0.95, has_error=True) == "invalid"


def test_review_queue_store_lifecycle_redacts_raw_content_and_uses_stable_ids() -> None:
    store = InMemoryReviewQueueStore()
    request = CreateReviewQueueItem(
        field="description",
        proposed_value={"raw_html": "<html>secret</html>", "summary": "Digital bank"},
        confidence=0.6,
        reason="low_confidence",
    )

    first = store.create(request, at=datetime(2026, 1, 1, tzinfo=UTC))
    second = store.create(request, at=datetime(2026, 1, 2, tzinfo=UTC))
    decided = store.decide(first.item_id, decision="approved", reason="verified", reviewer="ops")

    assert decided is not None
    assert first.item_id == second.item_id
    assert store.list() == (decided,)
    assert decided.status == "approved"
    assert decided.decision_reason == "verified"
    rendered = json.dumps(decided.model_dump(mode="json"), sort_keys=True)
    assert "<html>secret</html>" not in rendered
    assert "[redacted]" in rendered


def test_review_queue_store_redacts_secrets_and_keeps_decisions_terminal() -> None:
    store = InMemoryReviewQueueStore()
    item = store.create(
        CreateReviewQueueItem(
            field="credentials",
            proposed_value={"api_key": "sk-secret", "summary": "Digital bank"},
            current_value={"password": "super-secret"},
            confidence=0.4,
            reason="low_confidence",
        ),
        at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    approved = store.decide(item.item_id, decision="approved", reason="verified", reviewer="ops")
    rejected = store.decide(item.item_id, decision="rejected", reason="changed mind", reviewer="ops-2")

    assert approved is not None
    assert rejected == approved
    rendered = json.dumps(rejected.model_dump(mode="json"), sort_keys=True)
    assert "sk-secret" not in rendered
    assert "super-secret" not in rendered
    assert rejected.status == "approved"
    assert rejected.decision_reason == "verified"


def test_dashboard_summary_rolls_up_cost_and_freshness() -> None:
    sink = InMemoryObservabilitySink()
    now = datetime(2026, 5, 1, 12, tzinfo=UTC)
    sink.record_metric("crawl.tokens", 12, unit="tokens", occurred_at=now - timedelta(seconds=10))
    sink.record_metric("crawl.cost_usd", Decimal("0.03"), unit="usd", occurred_at=now - timedelta(days=2))
    sink.record_langfuse_event(
        "agent.output",
        output={"usage": {"total_tokens": 8, "cost_usd": "0.02"}},
        occurred_at=now - timedelta(seconds=20),
    )

    summary = summarize_dashboard(sink, now=now, stale_after_seconds=86_400)

    assert summary.cost.total_tokens == 20
    assert summary.cost.total_usd == Decimal("0.05")
    assert summary.cost.metric_count == 2
    assert summary.cost.langfuse_event_count == 1
    assert summary.freshness.observed_records == 3
    assert summary.freshness.stale_records == 1
    assert summary.freshness.max_age_seconds == 172_800
