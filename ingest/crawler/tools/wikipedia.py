from __future__ import annotations

from collections.abc import Mapping
from json import JSONDecodeError, dumps, loads
from urllib.parse import quote

from crawler.tools.http import AsyncHttpTransport, FetchTransportError, HttpResponse, RawDoc


class WikipediaFetchTool:
    def __init__(
        self,
        transport: AsyncHttpTransport,
        *,
        api_base_url: str = "https://en.wikipedia.org/api/rest_v1",
    ) -> None:
        self._transport = transport
        self._api_base_url = api_base_url.rstrip("/")
        self._headers = {
            "User-Agent": "neobanker-crawler/0.1 (+wikipedia-fetch)",
            "Accept": "application/json",
        }

    async def summary(self, title: str, *, timeout_seconds: float = 10.0) -> RawDoc:
        url = f"{self._api_base_url}/page/summary/{quote(title, safe='')}"
        response = await self._transport.request(
            "GET",
            url,
            headers=self._headers,
            timeout_seconds=timeout_seconds,
        )
        _raise_for_status(response)
        return _raw_from_response(response, fields=("extract", "description"))

    async def page_extract(self, title: str, *, timeout_seconds: float = 10.0) -> RawDoc:
        url = f"{self._api_base_url}/page/mobile-sections/{quote(title, safe='')}"
        response = await self._transport.request(
            "GET",
            url,
            headers=self._headers,
            timeout_seconds=timeout_seconds,
        )
        _raise_for_status(response)
        payload = _json_body(response)
        lead = payload.get("lead") if isinstance(payload, Mapping) else None
        sections = payload.get("remaining", {}).get("sections", []) if isinstance(payload, Mapping) else []
        texts: list[str] = []
        if isinstance(lead, Mapping) and isinstance(lead.get("extract"), str):
            texts.append(lead["extract"])
        if isinstance(sections, list):
            for section in sections:
                if isinstance(section, Mapping) and isinstance(section.get("text"), str):
                    texts.append(section["text"])
        text = "\n\n".join(texts)
        return RawDoc(
            source_url=response.url,
            content_type="text/plain",
            text=text,
            bytes=text.encode("utf-8"),
            metadata={"status_code": response.status_code, "title": title, "sections": len(texts)},
        )


def _raw_from_response(response: HttpResponse, *, fields: tuple[str, ...]) -> RawDoc:
    _raise_for_status(response)
    payload = _json_body(response)
    text_parts = [
        str(payload[field])
        for field in fields
        if isinstance(payload, Mapping) and isinstance(payload.get(field), str)
    ]
    text = "\n\n".join(text_parts)
    return RawDoc(
        source_url=response.url,
        content_type="application/json",
        text=text,
        bytes=dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"),
        metadata={
            "status_code": response.status_code,
            "title": payload.get("title") if isinstance(payload, Mapping) else None,
        },
    )


def _raise_for_status(response: HttpResponse) -> None:
    if response.status_code >= 400:
        raise FetchTransportError(f"Wikipedia HTTP {response.status_code} fetching {response.url!r}")


def _json_body(response: HttpResponse) -> object:
    try:
        return loads(response.body.decode("utf-8"))
    except (UnicodeDecodeError, JSONDecodeError) as exc:
        raise FetchTransportError(f"Wikipedia returned invalid JSON from {response.url!r}") from exc
