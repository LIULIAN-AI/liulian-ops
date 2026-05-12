from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from crawler.harness.checkpoint import JsonValue
from crawler.tools.http import FetchSizeError, RawDoc, validate_fetch_url


class BrowserPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_url: str = Field(min_length=1)
    html: str = Field(min_length=1)
    markdown: str | None = None
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class BrowserClient(Protocol):
    async def render(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> BrowserPage: ...


class BrowserFetchTool:
    def __init__(
        self,
        client: BrowserClient,
        *,
        allowed_hosts: tuple[str, ...] = (),
        default_headers: Mapping[str, str] | None = None,
        max_chars: int = 5_000_000,
    ) -> None:
        self._client = client
        self._allowed_hosts = allowed_hosts
        self._max_chars = max_chars
        self._default_headers = {
            "User-Agent": "neobanker-crawler/0.1 (+rendered-fetch)",
            "Accept-Language": "en-US,en;q=0.9",
        }
        if default_headers:
            self._default_headers.update(default_headers)

    async def fetch(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        timeout_seconds: float = 30.0,
    ) -> RawDoc:
        validate_fetch_url(url, allowed_hosts=self._allowed_hosts)
        request_headers = {**self._default_headers, **dict(headers or {})}
        page = await self._client.render(
            url,
            headers=request_headers,
            timeout_seconds=timeout_seconds,
        )
        validate_fetch_url(page.source_url, allowed_hosts=self._allowed_hosts)
        text = page.markdown or page.html
        if len(text) > self._max_chars:
            raise FetchSizeError(f"Rendered page from {page.source_url!r} exceeds {self._max_chars} chars")
        return RawDoc(
            source_url=page.source_url,
            content_type="text/markdown" if page.markdown else "text/html",
            text=text,
            metadata={
                **page.metadata,
                "rendered": True,
                "html_length": len(page.html),
                "markdown_length": len(page.markdown or ""),
            },
        )
