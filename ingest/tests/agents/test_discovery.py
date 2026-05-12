from __future__ import annotations

import asyncio
from typing import Any, cast

from crawler.agents.discovery import DiscoveryAgent, REGULATOR_SEEDS, WIKIPEDIA_SEEDS
from crawler.harness.checkpoint import JsonValue
from crawler.harness.planner import PlannedStep
from crawler.tools.qdrant import InMemoryQdrantTool


def test_discovery_normalizes_filters_and_orders_candidates() -> None:
    async def run() -> None:
        result = await DiscoveryAgent().run(
            _step(
                {
                    "candidates": [
                        {
                            "name": "Zeta Wallet",
                            "description": "Crypto wallet, not a bank",
                        },
                        {
                            "company_name": "  Beta Digital Bank | ",
                            "website": "https://www.beta.example/about",
                            "country": "US",
                            "description": "A branchless digital bank with a mobile bank account.",
                            "source_url": "https://search.example/b",
                        },
                    ],
                    "search_results": [
                        {
                            "title": "Alpha Neobank",
                            "url": "https://alpha.example",
                            "snippet": "Alpha is a neobank and banking app for freelancers.",
                        }
                    ],
                }
            )
        )

        records = cast(list[dict[str, Any]], result.state["pending_create"])
        assert [item["proposed_record"]["company_name"] for item in records] == [
            "Alpha Neobank",
            "Beta Digital Bank",
        ]
        assert records[0]["proposed_record"]["domain"] == "alpha.example"
        assert records[1]["proposed_record"]["domain"] == "beta.example"
        assert result.metadata["candidate_count"] == 2

    asyncio.run(run())


def test_discovery_dedups_existing_companies_by_domain_and_fuzzy_name() -> None:
    async def run() -> None:
        result = await DiscoveryAgent().run(
            _step(
                {
                    "existing_companies": [
                        {"company_name": "Monzo Bank Limited"},
                        {"company_name": "Other", "domain": "seen.example"},
                    ],
                    "candidates": [
                        {
                            "name": "Monzo",
                            "description": "A neobank and mobile bank.",
                            "domain": "monzo.example",
                        },
                        {
                            "name": "Seen Digital Bank",
                            "description": "A digital bank for teams.",
                            "domain": "https://seen.example/home",
                        },
                        {
                            "name": "Fresh Neobank",
                            "description": "A challenger bank and banking app.",
                            "domain": "fresh.example",
                        },
                    ],
                }
            )
        )

        assert result.metadata["pending_create_count"] == 1
        records = cast(list[dict[str, Any]], result.state["pending_create"])
        assert records[0]["proposed_record"]["company_name"] == "Fresh Neobank"

    asyncio.run(run())


def test_discovery_dedups_in_run_by_domain_and_name_and_strips_url_query() -> None:
    async def run() -> None:
        result = await DiscoveryAgent().run(
            _step(
                {
                    "candidates": [
                        {
                            "name": "Alpha Digital Bank",
                            "description": "A neobank and banking app.",
                        },
                        {
                            "name": "Alpha Digital Bank Limited",
                            "description": "A neobank and banking app.",
                        },
                        {
                            "name": "Beta Digital Bank",
                            "description": "A neobank and banking app.",
                            "url": "https://www.beta.example?utm=1#section",
                        },
                        {
                            "name": "Gamma Digital Bank",
                            "description": "A neobank and banking app.",
                            "url": "https://beta.example/about",
                        },
                    ],
                }
            )
        )

        records = cast(list[dict[str, Any]], result.state["pending_create"])
        assert len(records) == 2
        assert [record["action"] for record in records] == ["pending_create", "pending_create"]
        domains = [record["proposed_record"].get("domain") for record in records]
        assert domains == [None, "beta.example"]
        assert all("?" not in domain for domain in domains if isinstance(domain, str))

    asyncio.run(run())


def test_discovery_dedups_with_qdrant_similarity_index() -> None:
    async def run() -> None:
        index = InMemoryQdrantTool()
        index.upsert(
            "companies",
            [
                {
                    "id": "existing-chime",
                    "text": "Chime is a neobank mobile banking app digital bank",
                    "payload": {"company_name": "Chime"},
                }
            ],
        )

        result = await DiscoveryAgent(similarity_index=index).run(
            _step(
                {
                    "similarity_threshold": 0.7,
                    "candidates": [
                        {
                            "name": "Chime",
                            "description": "A neobank mobile banking app digital bank",
                        },
                        {
                            "name": "Nova Neobank",
                            "description": "A branchless digital bank.",
                        },
                    ],
                }
            )
        )

        assert result.state["pending_create"] == [
            {
                "action": "pending_create",
                "status": "pending",
                "proposed_record": {
                    "company_name": "Nova Neobank",
                    "confidence": 0.91,
                },
                "provenance": [{"source_name": "Nova Neobank", "source_type": "manual"}],
            }
        ]

    asyncio.run(run())


def test_discovery_outputs_pending_create_with_provenance_and_seeds() -> None:
    async def run() -> None:
        result = await DiscoveryAgent().run(
            _step(
                {
                    "include_seeds": True,
                    "candidates": [
                        {
                            "name": "Review Neobank",
                            "description": "A neobank for small businesses.",
                            "source_name": "Search API",
                            "source_type": "search",
                            "source_url": "https://search.example/review",
                        }
                    ],
                }
            )
        )

        records = cast(list[dict[str, Any]], result.state["pending_create"])
        assert records[0]["action"] == "pending_create"
        assert records[0]["status"] == "pending"
        assert records[0]["provenance"] == [
            {
                "source_name": "Search API",
                "source_type": "search",
                "source_url": "https://search.example/review",
            }
        ]
        assert result.state["discovery_seeds"] == [*WIKIPEDIA_SEEDS, *REGULATOR_SEEDS]

    asyncio.run(run())


def test_discovery_is_deterministic_and_strips_raw_content() -> None:
    async def run() -> None:
        candidates: list[JsonValue] = [
            {
                "name": "Bravo Neobank",
                "description": "A digital bank.",
                "raw_html": "<html>secret</html>",
            },
            {
                "name": "Alpha Neobank",
                "description": "A neobank.",
                "text": "secret page text",
                "source": "<html>" + ("secret" * 50) + "</html>",
            },
        ]
        params: dict[str, JsonValue] = {"candidates": candidates}
        first = await DiscoveryAgent().run(_step(params))
        second = await DiscoveryAgent().run(_step({"candidates": list(reversed(candidates))}))

        assert first.state == second.state
        assert "secret" not in str(first.state)
        records = cast(list[dict[str, Any]], first.state["pending_create"])
        assert [item["proposed_record"]["company_name"] for item in records] == [
            "Alpha Neobank",
            "Bravo Neobank",
        ]

    asyncio.run(run())


def test_u12_file_size_budget_is_under_400_lines() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    paths = [
        root / "src/crawler/agents/discovery.py",
        root / "src/crawler/tools/qdrant.py",
        root / "tests/agents/test_discovery.py",
        root / "tests/tools/test_qdrant.py",
    ]

    for path in paths:
        with path.open(encoding="utf-8") as handle:
            assert sum(1 for _line in handle) <= 400


def _step(params: dict[str, JsonValue]) -> PlannedStep:
    return PlannedStep(
        step_id="discover_banks",
        workflow="discover_banks",
        handler="discover_banks",
        params=params,
        dedup_key="discover_banks:discover_banks",
        sequence=1,
    )
