from __future__ import annotations

import asyncio
from collections.abc import Mapping

import pytest

from crawler.harness.checkpoint import JsonValue
from crawler.tools.app_store import AppStoreFetchTool
from crawler.tools.browser import BrowserFetchTool, BrowserPage
from crawler.tools.http import (
    FetchBlockedError,
    FetchSizeError,
    FetchTransportError,
    HttpFetchTool,
    HttpResponse,
    RawDoc,
    RetryPolicy,
)
from crawler.tools.pdf import PdfExtractionTool, PdfParseError, PdfParseResult
from crawler.tools.regulator import RegulatorFetchTool
from crawler.tools.wikipedia import WikipediaFetchTool


class FakeTransport:
    def __init__(self, responses: list[HttpResponse | BaseException]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str, Mapping[str, str]]] = []

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> HttpResponse:
        self.calls.append((method, url, dict(headers)))
        _ = timeout_seconds
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def test_http_fetch_retries_adds_respectful_headers_and_hashes_content() -> None:
    async def run() -> None:
        sleeps: list[float] = []
        transport = FakeTransport(
            [
                HttpResponse(url="https://example.test/a", status_code=503, body=b"busy"),
                HttpResponse(
                    url="https://example.test/a",
                    status_code=200,
                    headers={"Content-Type": "text/html; charset=utf-8"},
                    body=b"<html>ok</html>",
                ),
            ]
        )
        tool = HttpFetchTool(
            transport,
            allowed_hosts=("example.test",),
            retry_policy=RetryPolicy(attempts=2, backoff_seconds=(1.25,)),
            sleep=lambda seconds: sleeps.append(seconds),
        )

        document = await tool.fetch("https://example.test/a")

        assert document.source_url == "https://example.test/a"
        assert document.content_type == "text/html"
        assert document.text == "<html>ok</html>"
        assert document.bytes is None
        assert document.content_hash == "b6c951ed9485262465aa9b940f7e9b7df577e3a299a33f8c86fdda98f66a9fdc"
        assert sleeps == [1.25]
        assert len(transport.calls) == 2
        headers = transport.calls[0][2]
        assert headers["User-Agent"].startswith("neobanker-crawler/")
        assert headers["DNT"] == "1"

    asyncio.run(run())


def test_http_fetch_rechecks_redirect_final_url_allow_list() -> None:
    async def run() -> None:
        transport = FakeTransport(
            [HttpResponse(url="https://blocked.test/a", status_code=200, body=b"ok")]
        )
        tool = HttpFetchTool(transport, allowed_hosts=("allowed.test",))

        with pytest.raises(FetchBlockedError):
            await tool.fetch("https://allowed.test/a")

    asyncio.run(run())


def test_http_fetch_rejects_oversized_response() -> None:
    async def run() -> None:
        transport = FakeTransport(
            [HttpResponse(url="https://example.test/a", status_code=200, body=b"12345")]
        )
        tool = HttpFetchTool(transport, max_bytes=4)

        with pytest.raises(FetchSizeError):
            await tool.fetch("https://example.test/a")

    asyncio.run(run())


def test_http_allow_list_blocks_before_transport_is_called() -> None:
    async def run() -> None:
        transport = FakeTransport([])
        tool = HttpFetchTool(transport, allowed_hosts=("allowed.test",))

        with pytest.raises(FetchBlockedError):
            await tool.fetch("https://blocked.test/a")

        assert transport.calls == []

    asyncio.run(run())


def test_http_transport_errors_are_wrapped_after_retry() -> None:
    async def run() -> None:
        transport = FakeTransport([TimeoutError("slow"), TimeoutError("still slow")])
        tool = HttpFetchTool(transport, retry_policy=RetryPolicy(attempts=2, backoff_seconds=(0,)))

        with pytest.raises(FetchTransportError):
            await tool.fetch("https://example.test/a")

        assert len(transport.calls) == 2

    asyncio.run(run())


def test_browser_fetch_uses_injected_client_without_browser_install() -> None:
    class FakeBrowser:
        async def render(
            self,
            url: str,
            *,
            headers: Mapping[str, str],
            timeout_seconds: float,
        ) -> BrowserPage:
            assert "User-Agent" in headers
            assert timeout_seconds == 30.0
            return BrowserPage(
                source_url=url,
                html="<html><body>Rendered</body></html>",
                markdown="# Rendered",
            )

    async def run() -> None:
        document = await BrowserFetchTool(FakeBrowser(), allowed_hosts=("example.test",)).fetch(
            "https://example.test"
        )

        assert document.content_type == "text/markdown"
        assert document.text == "# Rendered"
        assert document.metadata["html_length"] == 34

    asyncio.run(run())


def test_browser_fetch_blocks_disallowed_url_before_render() -> None:
    class FakeBrowser:
        called = False

        async def render(
            self,
            url: str,
            *,
            headers: Mapping[str, str],
            timeout_seconds: float,
        ) -> BrowserPage:
            self.called = True
            return BrowserPage(source_url=url, html="<html>bad</html>")

    async def run() -> None:
        browser = FakeBrowser()
        with pytest.raises(FetchBlockedError):
            await BrowserFetchTool(browser, allowed_hosts=("allowed.test",)).fetch(
                "https://blocked.test"
            )
        assert browser.called is False

    asyncio.run(run())


def test_pdf_parser_errors_are_wrapped() -> None:
    class BrokenParser:
        async def parse(self, data: bytes, *, source_url: str) -> PdfParseResult:
            _ = data, source_url
            raise ValueError("bad pdf")

    async def run() -> None:
        source = RawDoc(source_url="https://example.test/a.pdf", content_type="application/pdf", bytes=b"%PDF")

        with pytest.raises(PdfParseError):
            await PdfExtractionTool(BrokenParser()).extract(source)

    asyncio.run(run())


def test_pdf_parser_returns_text_and_page_metadata() -> None:
    class FakeParser:
        async def parse(self, data: bytes, *, source_url: str) -> PdfParseResult:
            assert data == b"%PDF"
            assert source_url == "https://example.test/a.pdf"
            return PdfParseResult(text="Terms and disclosures", pages=2)

    async def run() -> None:
        source = RawDoc(source_url="https://example.test/a.pdf", content_type="application/pdf", bytes=b"%PDF")
        document = await PdfExtractionTool(FakeParser()).extract(source)

        assert document.text == "Terms and disclosures"
        assert document.metadata["pages"] == 2
        assert document.metadata["source_content_hash"] == source.content_hash

    asyncio.run(run())


def test_wikipedia_summary_parses_extract_from_injected_transport() -> None:
    async def run() -> None:
        transport = FakeTransport(
            [
                HttpResponse(
                    url="https://wiki.test/page/summary/Neo",
                    status_code=200,
                    body=b'{"title":"Neo","description":"bank","extract":"summary text"}',
                )
            ]
        )

        document = await WikipediaFetchTool(transport, api_base_url="https://wiki.test").summary("Neo")

        assert document.text == "summary text\n\nbank"
        assert document.metadata["title"] == "Neo"
        assert transport.calls[0][1] == "https://wiki.test/page/summary/Neo"

    asyncio.run(run())


def test_wikipedia_summary_raises_fetch_error_for_http_error() -> None:
    async def run() -> None:
        transport = FakeTransport(
            [HttpResponse(url="https://wiki.test/page/summary/Missing", status_code=404, body=b"{}")]
        )

        with pytest.raises(FetchTransportError):
            await WikipediaFetchTool(transport, api_base_url="https://wiki.test").summary("Missing")

    asyncio.run(run())


def test_app_store_fetch_uses_injected_structured_clients() -> None:
    class GoogleClient:
        async def lookup(
            self,
            app_id: str,
            *,
            country: str | None = None,
        ) -> Mapping[str, JsonValue]:
            return {"app_id": app_id, "country": country, "rating": 4.7}

    async def run() -> None:
        document = await AppStoreFetchTool(google_play=GoogleClient()).fetch(
            platform="google_play",
            app_id="com.example.bank",
            country="US",
        )

        assert document.source_url == "app-store://google-play/com.example.bank"
        assert document.text == '{"app_id":"com.example.bank","country":"US","rating":4.7}'
        assert document.metadata["platform"] == "google_play"

    asyncio.run(run())


def test_app_store_rejects_non_json_numbers() -> None:
    class GoogleClient:
        async def lookup(
            self,
            app_id: str,
            *,
            country: str | None = None,
        ) -> Mapping[str, JsonValue]:
            _ = app_id, country
            return {"rating": float("nan")}

    async def run() -> None:
        with pytest.raises(Exception, match="non-JSON|non-finite"):
            await AppStoreFetchTool(google_play=GoogleClient()).fetch(
                platform="google_play",
                app_id="com.example.bank",
            )

    asyncio.run(run())


def test_raw_doc_rejects_mismatched_content_hash() -> None:
    with pytest.raises(ValueError, match="content_hash"):
        RawDoc(source_url="https://example.test", text="new", content_hash="0" * 64)


def test_regulator_fetch_wraps_http_tool() -> None:
    async def run() -> None:
        transport = FakeTransport(
            [
                HttpResponse(
                    url="https://regulator.test/list",
                    status_code=200,
                    headers={"content-type": "application/json"},
                    body=b'{"items":[]}',
                )
            ]
        )
        document = await RegulatorFetchTool(
            HttpFetchTool(transport, allowed_hosts=("regulator.test",))
        ).fetch("https://regulator.test/list")

        assert document.text == '{"items":[]}'
        assert document.metadata["source_type"] == "regulator"

    asyncio.run(run())
