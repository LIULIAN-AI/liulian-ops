from __future__ import annotations

import asyncio
from collections.abc import Mapping
import json
from pathlib import Path
from typing import Any

import pytest

from crawler.agents.base import AgentToolError, ToolRegistry
from crawler.agents.writer import BACKEND_UPSERT_TOOL, WriterAgent
from crawler.harness.checkpoint import JsonValue
from crawler.harness.planner import PlannedStep
from crawler.store.backend_client import (
    BackendClient,
    BackendClientAuthError,
    BackendClientConfig,
    BackendClientJsonError,
    BackendClientResponseError,
    BackendHttpResponse,
    BackendUpsertResult,
    INTERNAL_KEY_HEADER,
)


def test_backend_client_posts_internal_upsert_with_key_and_typed_result() -> None:
    async def run() -> None:
        transport = _FakeTransport(BackendHttpResponse(status_code=200, body=_json({"applied": True})))
        client = BackendClient(
            BackendClientConfig(base_url="https://backend.test/", internal_key="secret"),
            transport=transport,
        )

        result = await client.upsert({"write_payload": {"company_name": "Neo Bank"}})

        assert result == BackendUpsertResult(applied=True)
        assert transport.calls == [
            {
                "url": "https://backend.test/internal/crawler/upsert",
                "headers": {
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    INTERNAL_KEY_HEADER: "secret",
                },
                "json_body": {"write_payload": {"company_name": "Neo Bank"}},
                "timeout_seconds": 10.0,
            }
        ]

    asyncio.run(run())


def test_writer_auto_applies_with_backend_client_and_review_context() -> None:
    async def run() -> None:
        client = _FakeClient(
            BackendUpsertResult(
                applied=True,
                record_id="bank-1",
                version=3,
                metadata={"review": "stored"},
            )
        )
        result = await WriterAgent(backend_client=client).run(
            _step(
                {
                    "field_group": "about",
                    "bank_id": "bank-1",
                    "decision": "auto_apply",
                    "write_payload": {"company_name": "Neo Bank", "confidence": 0.95},
                    "provenance": {"source_url": "https://example.test/about", "content_hash": "abc"},
                    "review_queue": [{"field": "company_name", "status": "pending"}],
                }
            )
        )

        assert result.metadata["applied"] is True
        assert result.metadata["record_id"] == "bank-1"
        assert client.requests[0]["write_payload"] == {"company_name": "Neo Bank", "confidence": 0.95}
        assert client.requests[0]["provenance"] == {
            "content_hash": "abc",
            "source_url": "https://example.test/about",
        }
        assert result.state["write_keys"] == ["company_name", "confidence"]
        assert result.state["review_queue"] == [{"field": "company_name", "status": "pending"}]

    asyncio.run(run())


def test_writer_can_use_registered_http_tool_without_raw_db() -> None:
    async def upsert(request: JsonValue) -> dict[str, JsonValue]:
        calls.append(request)
        return {"applied": True, "needs_review": False, "review_queue": []}

    async def run() -> None:
        registry = ToolRegistry()
        registry.register(BACKEND_UPSERT_TOOL, upsert)
        result = await WriterAgent(registry=registry).run(
            _step({"decision": "auto_apply", "write_payload": {"company_name": "Neo Bank"}})
        )

        assert result.metadata["applied"] is True
        assert len(calls) == 1

    calls: list[JsonValue] = []
    asyncio.run(run())


def test_writer_rejects_registered_mysql_writing_tool() -> None:
    async def upsert(request: JsonValue) -> dict[str, JsonValue]:
        _ = request
        return {"applied": True}

    async def run() -> None:
        registry = ToolRegistry()
        registry.register(BACKEND_UPSERT_TOOL, upsert, writes_mysql=True)

        with pytest.raises(AgentToolError, match="writes to MySQL"):
            await WriterAgent(registry=registry).run(
                _step({"decision": "auto_apply", "write_payload": {"company_name": "Neo Bank"}})
            )

    asyncio.run(run())


def test_writer_skips_needs_review_invalid_and_empty_payload_without_network() -> None:
    async def run() -> None:
        client = _FakeClient(BackendUpsertResult(applied=True))
        needs_review = await WriterAgent(backend_client=client).run(
            _step({"decision": "needs_review", "write_payload": {"company_name": "Neo Bank"}})
        )
        invalid = await WriterAgent(backend_client=client).run(
            _step({"decision": "invalid", "write_payload": {"company_name": "Neo Bank"}})
        )
        empty = await WriterAgent(backend_client=client).run(
            _step({"decision": "auto_apply", "write_payload": {}})
        )

        assert needs_review.metadata["skipped_reason"] == "validation_not_auto_apply"
        assert invalid.metadata["skipped_reason"] == "validation_not_auto_apply"
        assert empty.metadata["skipped_reason"] == "empty_write_payload"
        assert client.requests == []

    asyncio.run(run())


def test_writer_skips_payload_that_is_empty_after_raw_stripping() -> None:
    async def run() -> None:
        client = _FakeClient(BackendUpsertResult(applied=True))
        result = await WriterAgent(backend_client=client).run(
            _step(
                {
                    "decision": "auto_apply",
                    "write_payload": {"raw_html": "<html>request secret</html>"},
                }
            )
        )

        assert result.metadata["skipped_reason"] == "empty_write_payload_after_cleaning"
        assert client.requests == []

    asyncio.run(run())


def test_backend_client_missing_internal_key_and_backend_errors_are_clean() -> None:
    async def run() -> None:
        missing_key_client = BackendClient(
            BackendClientConfig(base_url="https://backend.test", internal_key=""),
            transport=_FakeTransport(BackendHttpResponse(status_code=200, body=_json({"applied": True}))),
        )
        error_client = BackendClient(
            BackendClientConfig(base_url="https://backend.test", internal_key="secret"),
            transport=_FakeTransport(BackendHttpResponse(status_code=503, body=b"down")),
        )
        invalid_json_client = BackendClient(
            BackendClientConfig(base_url="https://backend.test", internal_key="secret"),
            transport=_FakeTransport(BackendHttpResponse(status_code=200, body=b"not-json")),
        )
        invalid_shape_client = BackendClient(
            BackendClientConfig(base_url="https://backend.test", internal_key="secret"),
            transport=_FakeTransport(BackendHttpResponse(status_code=200, body=_json({"review_queue": {}}))),
        )

        with pytest.raises(BackendClientAuthError, match="AGENT_INTERNAL_KEY"):
            await missing_key_client.upsert({"write_payload": {}})
        with pytest.raises(BackendClientResponseError, match="HTTP 503"):
            await error_client.upsert({"write_payload": {}})
        with pytest.raises(BackendClientJsonError, match="invalid JSON"):
            await invalid_json_client.upsert({"write_payload": {}})
        with pytest.raises(BackendClientJsonError, match="did not match schema"):
            await invalid_shape_client.upsert({"write_payload": {}})

    asyncio.run(run())


def test_writer_result_does_not_leak_raw_source_content() -> None:
    async def run() -> None:
        client = _FakeClient(
            BackendUpsertResult(
                applied=True,
                metadata={"raw_html": "<html>backend secret</html>", "safe": "ok"},
            )
        )
        result = await WriterAgent(backend_client=client).run(
            _step(
                {
                    "decision": "auto_apply",
                    "write_payload": {
                        "company_name": "Neo Bank",
                        "raw_html": "<html>request secret</html>",
                    },
                    "provenance": {"markdown": "request secret", "source_url": "https://example.test"},
                }
            )
        )

        assert "secret" not in str(result.metadata)
        assert "secret" not in str(result.state)
        assert "secret" not in str(client.requests)

    asyncio.run(run())


def test_u10_python_files_stay_under_line_budget() -> None:
    root = Path(__file__).resolve().parents[2]
    files = [
        root / "src/crawler/store/backend_client.py",
        root / "src/crawler/agents/writer.py",
        root / "tests/agents/test_writer.py",
    ]

    assert all(len(path.read_text().splitlines()) <= 400 for path in files)


class _FakeTransport:
    def __init__(self, response: BackendHttpResponse) -> None:
        self._response = response
        self.calls: list[dict[str, Any]] = []

    async def post_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        json_body: Mapping[str, JsonValue],
        timeout_seconds: float,
    ) -> BackendHttpResponse:
        self.calls.append(
            {
                "url": url,
                "headers": dict(headers),
                "json_body": dict(json_body),
                "timeout_seconds": timeout_seconds,
            }
        )
        return self._response


class _FakeClient:
    def __init__(self, result: BackendUpsertResult) -> None:
        self._result = result
        self.requests: list[dict[str, JsonValue]] = []

    async def upsert(self, payload: Mapping[str, JsonValue]) -> BackendUpsertResult:
        self.requests.append(dict(payload))
        return self._result


def _json(value: Mapping[str, JsonValue]) -> bytes:
    return json.dumps(dict(value), sort_keys=True).encode("utf-8")


def _step(params: dict[str, JsonValue]) -> PlannedStep:
    return PlannedStep(
        step_id="writer",
        workflow="refresh_field_group",
        handler="writer",
        params=params,
        dedup_key="refresh_field_group:writer",
        sequence=1,
    )
