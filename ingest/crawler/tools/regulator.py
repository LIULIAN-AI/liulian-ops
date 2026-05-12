from __future__ import annotations

from collections.abc import Mapping

from crawler.tools.http import HttpFetchTool, RawDoc


class RegulatorFetchTool:
    def __init__(self, http: HttpFetchTool) -> None:
        self._http = http

    async def fetch(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        timeout_seconds: float = 15.0,
    ) -> RawDoc:
        document = await self._http.fetch(
            url,
            headers=headers,
            timeout_seconds=timeout_seconds,
        )
        return document.model_copy(
            update={"metadata": {**document.metadata, "source_type": "regulator"}}
        )
