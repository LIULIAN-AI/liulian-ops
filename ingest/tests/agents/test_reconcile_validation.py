from __future__ import annotations

import asyncio
from typing import cast

from crawler.agents.reconcile import ReconcileAgent
from crawler.agents.validation import ValidationAgent
from crawler.harness.checkpoint import JsonValue
from crawler.harness.planner import PlannedStep


def test_reconcile_agent_deterministically_merges_consensus_candidates() -> None:
    async def run() -> None:
        candidates: list[JsonValue] = [
            {
                "extracted": {"company_name": "Neo Bank", "description": "Digital bank", "confidence": 0.91},
                "provenance": {
                    "source_url": "https://b.example/about",
                    "content_hash": "hash-b",
                    "llm_model": "local",
                    "confidence": 0.91,
                },
            },
            {
                "extracted": {"company_name": "Neo Bank", "description": "Digital bank", "confidence": 0.86},
                "provenance": {
                    "source_url": "https://a.example/about",
                    "content_hash": "hash-a",
                    "llm_model": "local",
                    "confidence": 0.86,
                },
            },
        ]

        result_one = await ReconcileAgent().run(_step("reconcile", {"field_group": "about", "candidates": candidates}))
        result_two = await ReconcileAgent().run(
            _step("reconcile", {"field_group": "about", "candidates": list(reversed(candidates))})
        )

        assert result_one.state == result_two.state
        assert result_one.state["normalized_record"] == {
            "company_name": "Neo Bank",
            "description": "Digital bank",
            "confidence": 0.915,
        }
        assert result_one.metadata["conflict_count"] == 0
        assert "https://a.example/about" in str(result_one.state)

    asyncio.run(run())


def test_reconcile_agent_marks_conflicts_and_validation_routes_review() -> None:
    async def run() -> None:
        reconciled = await ReconcileAgent().run(
            _step(
                "reconcile",
                {
                    "field_group": "about",
                    "field_candidates": [
                        {
                            "field_name": "headquarters",
                            "value": "Brazil",
                            "confidence": 0.91,
                            "source_url": "https://official.example",
                        },
                        {
                            "field_name": "headquarters",
                            "value": "United States",
                            "confidence": 0.89,
                            "source_url": "https://registry.example",
                        },
                    ],
                },
            )
        )

        validated = await ValidationAgent().run(
            _step(
                "validation",
                {
                    "field_group": "about",
                    "normalized_record": reconciled.state["normalized_record"],
                    "reconciled_fields": reconciled.state["reconciled_fields"],
                    "current_record": {"headquarters": "United States"},
                },
            )
        )

        assert reconciled.metadata["conflict_count"] == 1
        assert validated.metadata["decision"] == "needs_review"
        assert validated.state["write_payload"] == {}
        assert validated.state["review_queue"] == [
            {
                "field": "headquarters",
                "proposed_value": "Brazil",
                "current_value": "United States",
                "confidence": 0.91,
                "reason": "conflicting_values",
                "status": "pending",
            }
        ]

    asyncio.run(run())


def test_validation_agent_routes_low_confidence_to_review_queue() -> None:
    async def run() -> None:
        result = await ValidationAgent().run(
            _step(
                "validation",
                {
                    "field_group": "about",
                    "reconciled_fields": [
                        {
                            "field_name": "company_name",
                            "value": "Neo Bank",
                            "confidence": 0.62,
                            "decision": "accepted",
                        },
                        {
                            "field_name": "description",
                            "value": "Digital bank",
                            "confidence": 0.81,
                            "decision": "accepted",
                        },
                    ],
                    "confidence_threshold": 0.75,
                },
            )
        )

        assert result.metadata["decision"] == "needs_review"
        assert result.state["review_queue"] == [
            {
                "field": "company_name",
                "proposed_value": "Neo Bank",
                "current_value": None,
                "confidence": 0.62,
                "reason": "low_confidence",
                "status": "pending",
            }
        ]

    asyncio.run(run())


def test_validation_agent_reports_business_rule_and_pydantic_failures() -> None:
    async def run() -> None:
        result = await ValidationAgent().run(
            _step(
                "validation",
                {
                    "field_group": "about",
                    "normalized_record": {
                        "company_name": "Neo Bank",
                        "description": "Digital bank",
                        "founded_year": "not-a-year",
                        "country_code": "USA",
                        "bank_swift": "bad-code",
                        "confidence": 0.9,
                    },
                },
            )
        )

        assert result.metadata["decision"] == "invalid"
        assert result.state["write_payload"] == {}
        errors = result.state["validation_errors"]
        assert isinstance(errors, list)
        assert {"field": "country_code", "reason": "invalid_iso_country_code"} in errors
        assert {"field": "bank_swift", "reason": "invalid_swift_code"} in errors
        assert {"field": "founded_year", "reason": "int_parsing"} in errors

    asyncio.run(run())


def test_validation_agent_routes_low_confidence_normalized_record_to_review() -> None:
    async def run() -> None:
        result = await ValidationAgent().run(
            _step(
                "validation",
                {
                    "field_group": "about",
                    "normalized_record": {
                        "company_name": "Neo Bank",
                        "confidence": 0.1,
                    },
                    "confidence_threshold": 0.75,
                },
            )
        )

        assert result.metadata["decision"] == "needs_review"
        assert result.state["write_payload"] == {}
        assert result.state["review_queue"] == [
            {
                "field": "company_name",
                "proposed_value": "Neo Bank",
                "current_value": None,
                "confidence": 0.1,
                "reason": "low_confidence",
                "status": "pending",
            }
        ]

    asyncio.run(run())


def test_validation_agent_rejects_unsupported_field_group() -> None:
    async def run() -> None:
        result = await ValidationAgent().run(
            _step(
                "validation",
                {
                    "field_group": "unknown_xyz",
                    "normalized_record": {
                        "company_name": "Neo Bank",
                        "confidence": 0.95,
                    },
                },
            )
        )

        assert result.metadata["decision"] == "invalid"
        assert result.state["write_payload"] == {}
        assert result.state["validation_errors"] == [
            {"field": "field_group", "reason": "unsupported_field_group"}
        ]

    asyncio.run(run())


def test_reconcile_and_validation_do_not_leak_raw_html_or_markdown() -> None:
    async def run() -> None:
        reconciled = await ReconcileAgent().run(
            _step(
                "reconcile",
                {
                    "field_group": "about",
                    "candidates": [
                        {
                            "extracted": {
                                "company_name": "Neo Bank",
                                "raw_html": "<html>secret page</html>",
                                "description": "<html>secret body</html>",
                                "confidence": 0.9,
                            },
                            "provenance": {
                                "source_url": "https://example.test",
                                "confidence": 0.9,
                            },
                        }
                    ],
                },
            )
        )
        validated = await ValidationAgent().run(
            _step(
                "validation",
                {
                    "field_group": "about",
                    "normalized_record": {
                        "company_name": "Neo Bank",
                        "description": "<html>secret body</html>",
                        "confidence": 0.9,
                    },
                },
            )
        )

        assert reconciled.metadata["dropped_raw_or_invalid_count"] == 1
        assert "secret" not in str(reconciled.state)
        assert "secret" not in str(validated.state)
        assert validated.metadata["decision"] == "invalid"

    asyncio.run(run())


def test_validation_agent_sanitizes_review_current_value_and_invalid_state_reasoning() -> None:
    async def run() -> None:
        review_result = await ValidationAgent().run(
            _step(
                "validation",
                {
                    "field_group": "about",
                    "reconciled_fields": [
                        {
                            "field_name": "description",
                            "value": "Digital bank",
                            "confidence": 0.6,
                            "decision": "accepted",
                        }
                    ],
                    "current_record": {"description": "<html>current secret</html>"},
                },
            )
        )
        invalid_result = await ValidationAgent().run(
            _step(
                "validation",
                {
                    "field_group": "about",
                    "normalized_record": {
                        "company_name": "Neo Bank",
                        "chain_of_thought": "private reasoning",
                        "founded_year": "bad",
                        "confidence": 0.9,
                    },
                },
            )
        )

        review_queue = cast(list[dict[str, JsonValue]], review_result.state["review_queue"])

        assert review_queue[0]["current_value"] is None
        assert "current secret" not in str(review_result.state)
        assert invalid_result.metadata["decision"] == "invalid"
        assert invalid_result.state["normalized_record"] == {}
        assert "private reasoning" not in str(invalid_result.state)

    asyncio.run(run())


def test_u09_python_files_stay_under_line_budget() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    files = [
        root / "src/crawler/agents/reconcile.py",
        root / "src/crawler/agents/validation.py",
        root / "tests/agents/test_reconcile_validation.py",
    ]

    line_counts = {path.name: len(path.read_text().splitlines()) for path in files}

    assert line_counts
    assert all(count <= 400 for count in line_counts.values())


def _step(handler: str, params: dict[str, JsonValue]) -> PlannedStep:
    return PlannedStep(
        step_id=handler,
        workflow="refresh_field_group",
        handler=handler,
        params=params,
        dedup_key=f"refresh_field_group:{handler}",
        sequence=1,
    )
