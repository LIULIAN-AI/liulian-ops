from __future__ import annotations

from pathlib import Path

from crawler.agents.source_selector import (
    DataDictionary,
    SourceRegistry,
    load_data_dictionary,
    load_source_registry,
    render_url_template,
)


def test_registry_parses_seed_sources() -> None:
    registry = load_source_registry(_config_path("sources.yaml"))

    source_ids = {source.id for source in registry.sources}

    assert {
        "official_site",
        "wikipedia",
        "google_play",
        "app_store",
        "annual_report",
        "bank_code",
        "regulator",
        "search",
        "pitchbook",
    } <= source_ids
    assert all(source.adapter and source.tool for source in registry.sources)


def test_data_dictionary_parses_seed_field_groups() -> None:
    dictionary = load_data_dictionary(_config_path("data_dictionary.yaml"))

    assert isinstance(dictionary, DataDictionary)
    assert {"about", "product", "financials", "staff", "tech", "marketing", "web3"} <= set(
        dictionary.field_groups
    )
    assert dictionary.field_groups["about"][0].name == "name"


def test_field_group_matching_supports_exact_and_prefix_groups() -> None:
    registry = SourceRegistry.model_validate(
        {
            "sources": [
                _source("marketing_parent", field_groups=["marketing"]),
                _source("marketing_app", field_groups=["marketing.app"]),
                _source("about", field_groups=["about"]),
            ]
        }
    )

    candidates = registry.candidates(field_group="marketing.app", company={})

    assert [candidate["source_id"] for candidate in candidates] == [
        "marketing_parent",
        "marketing_app",
    ]

    parent_candidates = registry.candidates(field_group="marketing", company={})
    assert [candidate["source_id"] for candidate in parent_candidates] == ["marketing_parent"]


def test_url_template_expands_from_company_dict_without_eval() -> None:
    rendered, missing = render_url_template(
        "https://{domain}/apps/{mobile.google_play_id}",
        {"domain": "example.com/", "mobile": {"google_play_id": "com.example"}},
    )

    assert rendered == "https://example.com/apps/com.example"
    assert missing == ()


def test_missing_template_variable_is_reported_and_left_unexpanded() -> None:
    rendered, missing = render_url_template("https://apps.test/{missing_id}", {"name": "Neo"})

    assert rendered == "https://apps.test/{missing_id}"
    assert missing == ("missing_id",)


def test_source_ordering_and_cost_filters() -> None:
    registry = SourceRegistry.model_validate(
        {
            "sources": [
                _source("paid", cost_tier="high"),
                _source("free_b", cost_tier="low"),
                _source("mid", cost_tier="medium"),
                _source("free_a", cost_tier="low"),
            ]
        }
    )

    all_candidates = registry.candidates(field_group="about", company={})
    filtered = registry.candidates(field_group="about", company={}, max_cost_tier="medium")

    assert [candidate["source_id"] for candidate in all_candidates] == [
        "free_b",
        "free_a",
        "mid",
        "paid",
    ]
    assert [candidate["source_id"] for candidate in filtered] == ["free_b", "free_a", "mid"]


def test_url_template_rejects_host_rewrite_values() -> None:
    import pytest

    from crawler.agents.source_selector import SourceConfigError

    with pytest.raises(SourceConfigError):
        render_url_template("https://{domain}", {"domain": "example.com@evil.test"})


def test_rendered_params_expand_query_template() -> None:
    registry = SourceRegistry.model_validate(
        {
            "sources": [
                _source(
                    "search",
                    field_groups=["*"],
                    params={"query_template": "{name} neobank {field_group}"},
                )
            ]
        }
    )

    candidates = registry.candidates(field_group="about", company={"name": "Neo"})

    assert candidates[0]["rendered_params"] == {
        "query_template": "Neo neobank about",
        "url": "https://{domain}",
    }
    assert candidates[0]["missing_template_variables"] == ["domain"]


def test_requested_source_id_bypasses_group_and_cost_filters() -> None:
    registry = SourceRegistry.model_validate(
        {"sources": [_source("paid", cost_tier="high", field_groups=["financials"])]}
    )

    candidates = registry.candidates(
        field_group="about",
        company={},
        source_id="paid",
        max_cost_tier="low",
    )

    assert [candidate["source_id"] for candidate in candidates] == ["paid"]


def _config_path(name: str) -> Path:
    return Path(__file__).resolve().parents[2] / "config" / name


def _source(
    source_id: str,
    *,
    cost_tier: str = "low",
    field_groups: list[str] | None = None,
    params: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "id": source_id,
        "source_type": "http",
        "adapter": source_id,
        "tool": "fetch.http",
        "cadence": "weekly",
        "cost_tier": cost_tier,
        "field_groups": field_groups or ["about"],
        "url_template": "https://{domain}",
        "params": params or {},
    }
