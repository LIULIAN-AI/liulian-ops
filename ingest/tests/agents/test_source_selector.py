from __future__ import annotations

import asyncio

from crawler.agents.source_selector import DataDictionary, SourceRegistry, SourceSelectorAgent
from crawler.harness.checkpoint import JsonValue
from crawler.harness.planner import PlannedStep


def test_source_selector_agent_returns_step_result_shape_and_no_raw_content() -> None:
    async def run() -> None:
        agent = SourceSelectorAgent(
            registry_config=SourceRegistry.model_validate(
                {
                    "sources": [
                        {
                            "id": "official_site",
                            "source_type": "http",
                            "adapter": "official_site",
                            "tool": "fetch.http",
                            "cadence": "weekly",
                            "cost_tier": "low",
                            "field_groups": ["about"],
                            "url_template": "https://{domain}",
                            "params": {"hint": "summary-only"},
                        }
                    ]
                }
            ),
            data_dictionary=DataDictionary.model_validate(
                {
                    "field_groups": {
                        "about": [
                            {
                                "name": "description",
                                "type": "string",
                                "description": "Short description",
                            }
                        ]
                    }
                }
            ),
        )

        result = await agent.run(
            _step(
                {
                    "field_group": "about",
                    "company_sort_id": "neo",
                    "company": {
                        "domain": "example.test",
                        "raw_html": "<html>secret</html>",
                    },
                }
            )
        )

        assert result.tokens == 0
        assert result.usd == 0
        assert result.metadata == {
            "field_group": "about",
            "candidate_count": 1,
            "known_field_groups": ["about"],
            "requested_source_id": None,
        }
        assert result.state == {
            "source_candidates": [
                {
                    "source_id": "official_site",
                    "source_type": "http",
                    "adapter": "official_site",
                    "tool": "fetch.http",
                    "cadence": "weekly",
                    "cost_tier": "low",
                    "url_template": "https://{domain}",
                    "rendered_params": {
                        "hint": "summary-only",
                        "url": "https://example.test",
                    },
                }
            ]
        }
        assert "secret" not in str(result.metadata)
        assert "secret" not in str(result.state)

    asyncio.run(run())


def test_source_selector_agent_reports_missing_template_variables() -> None:
    async def run() -> None:
        agent = SourceSelectorAgent(
            registry_config=SourceRegistry.model_validate(
                {
                    "sources": [
                        {
                            "id": "app_store",
                            "source_type": "app_store",
                            "adapter": "apple_app_store",
                            "tool": "fetch.app_store",
                            "cadence": "weekly",
                            "cost_tier": "low",
                            "field_groups": ["marketing.app"],
                            "url_template": "https://apps.apple.com/app/id{apple_app_store_id}",
                        }
                    ]
                }
            ),
            data_dictionary=DataDictionary.model_validate({"field_groups": {}}),
        )

        result = await agent.run(_step({"field_group": "marketing.app", "sort_id": "neo"}))

        assert result.state["source_candidates"] == [
            {
                "source_id": "app_store",
                "source_type": "app_store",
                "adapter": "apple_app_store",
                "tool": "fetch.app_store",
                "cadence": "weekly",
                "cost_tier": "low",
                "url_template": "https://apps.apple.com/app/id{apple_app_store_id}",
                "rendered_params": {
                    "url": "https://apps.apple.com/app/id{apple_app_store_id}",
                },
                "missing_template_variables": ["apple_app_store_id"],
            }
        ]

    asyncio.run(run())


def _step(params: dict[str, JsonValue]) -> PlannedStep:
    return PlannedStep(
        step_id="source_selector",
        workflow="refresh_field_group",
        handler="source_selector",
        params=params,
        dedup_key="refresh_field_group:source_selector",
        sequence=1,
    )
