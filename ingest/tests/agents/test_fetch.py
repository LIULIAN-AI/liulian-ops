from __future__ import annotations

import asyncio

import pytest

from crawler.agents.base import AgentToolError, ToolRegistry
from crawler.agents.fetch import FetchAgent
from crawler.harness.checkpoint import JsonValue
from crawler.harness.planner import PlannedStep
from crawler.tools.http import FetchBlockedError, RawDoc


def test_fetch_agent_routes_by_source_type_and_reports_zero_llm_usage() -> None:
    async def run() -> None:
        calls: list[dict[str, object]] = []

        async def http_tool(**kwargs: object) -> RawDoc:
            calls.append(kwargs)
            return RawDoc(
                source_url=str(kwargs["url"]),
                content_type="text/html",
                text="<html>secret</html>",
            )

        registry = ToolRegistry()
        registry.register("fetch.http", http_tool)

        result = await FetchAgent(registry=registry).run(
            _step({"source_type": "http", "url": "https://example.test"})
        )

        assert calls == [{"url": "https://example.test"}]
        assert result.tokens == 0
        assert result.usd == 0
        assert result.metadata["source_type"] == "http"
        assert result.metadata["tool_name"] == "fetch.http"
        assert result.metadata["content_hash"] == "85d12d6ee86194efeccda40478e57837e268e370929d59aa2aeaf07829b27496"
        assert "secret" not in str(result.metadata)
        assert "secret" not in str(result.state)

    asyncio.run(run())


def test_fetch_agent_omits_long_arbitrary_tool_metadata() -> None:
    async def run() -> None:
        async def http_tool(**kwargs: object) -> RawDoc:
            _ = kwargs
            return RawDoc(
                source_url="https://example.test",
                content_type="text/html",
                text="ok",
                metadata={
                    "payload": "<html>" + ("secret" * 100) + "</html>",
                    "debug": {"payload": "<html>" + ("nested" * 100) + "</html>"},
                    "items": ["<html>" + ("listed" * 100) + "</html>"],
                },
            )

        registry = ToolRegistry()
        registry.register("fetch.http", http_tool)

        result = await FetchAgent(registry=registry).run(
            _step({"source_type": "http", "url": "https://example.test"})
        )

        assert result.metadata["tool_metadata"] == {
            "debug": {"payload": "[omitted:613 chars]"},
            "items": ["[omitted:613 chars]"],
            "payload": "[omitted:613 chars]",
        }
        assert "secret" not in str(result.metadata)
        assert "nested" not in str(result.metadata)
        assert "listed" not in str(result.metadata)

    asyncio.run(run())


def test_fetch_agent_declared_tool_allow_list_rejects_missing_tool_registration() -> None:
    async def run() -> None:
        with pytest.raises(AgentToolError, match="not registered"):
            await FetchAgent(registry=ToolRegistry()).run(
                _step({"source_type": "browser", "url": "https://example.test"})
            )

    asyncio.run(run())


def test_fetch_agent_integrates_tool_allow_list_policy() -> None:
    async def run() -> None:
        async def blocked_http_tool(**kwargs: object) -> RawDoc:
            _ = kwargs
            raise FetchBlockedError("blocked by allow-list")

        registry = ToolRegistry()
        registry.register("fetch.http", blocked_http_tool)

        with pytest.raises(FetchBlockedError):
            await FetchAgent(registry=registry).run(
                _step({"source_type": "http", "url": "https://blocked.test"})
            )

    asyncio.run(run())


def test_fetch_agent_preserves_declared_fetch_tool_boundary() -> None:
    async def run() -> None:
        async def undeclared_tool(**kwargs: object) -> RawDoc:
            _ = kwargs
            return RawDoc(source_url="x://unused", content_type="text/plain", text="unused")

        config = FetchAgent.config.model_copy(update={"tool_names": ("fetch.http",)})
        registry = ToolRegistry()
        registry.register("fetch.wikipedia", undeclared_tool)

        with pytest.raises(AgentToolError, match="not declared"):
            await FetchAgent(registry=registry, config=config).run(
                _step({"source_type": "wikipedia", "title": "Neo"})
            )

    asyncio.run(run())


def _step(params: dict[str, JsonValue]) -> PlannedStep:
    return PlannedStep(
        step_id="fetch",
        workflow="discover_banks",
        handler="fetch",
        params=params,
        dedup_key="discover_banks:fetch",
        sequence=1,
    )
