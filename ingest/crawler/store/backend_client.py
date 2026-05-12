from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
import json
import os
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from crawler.harness.checkpoint import JsonValue

INTERNAL_UPSERT_PATH = "/internal/crawler/upsert"
INTERNAL_KEY_HEADER = "X-Agent-Internal-Key"


class BackendClientError(RuntimeError):
    """Base backend client failure."""


class BackendClientAuthError(BackendClientError):
    """Raised when no internal key is available."""


class BackendClientResponseError(BackendClientError):
    """Raised when the backend returns a non-2xx response."""


class BackendClientJsonError(BackendClientError):
    """Raised when the backend response is not valid JSON."""


class BackendHttpResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    status_code: int = Field(ge=100, le=599)
    body: bytes = b""
    headers: dict[str, str] = Field(default_factory=dict)


class AsyncBackendTransport(Protocol):
    async def post_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        json_body: Mapping[str, JsonValue],
        timeout_seconds: float,
    ) -> BackendHttpResponse: ...


class BackendUpsertResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="allow")

    applied: bool = False
    needs_review: bool = False
    review_queue: list[JsonValue] = Field(default_factory=list)
    record_id: str | None = None
    version: int | None = None
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


@dataclass(frozen=True)
class BackendClientConfig:
    base_url: str
    internal_key: str | None = None
    timeout_seconds: float = 10.0


class BackendClient:
    def __init__(
        self,
        config: BackendClientConfig,
        *,
        transport: AsyncBackendTransport | None = None,
    ) -> None:
        self._config = config
        self._transport = transport or UrlLibBackendTransport()

    async def upsert(self, payload: Mapping[str, JsonValue]) -> BackendUpsertResult:
        internal_key = self._internal_key()
        response = await self._transport.post_json(
            self._url(INTERNAL_UPSERT_PATH),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                INTERNAL_KEY_HEADER: internal_key,
            },
            json_body=dict(payload),
            timeout_seconds=self._config.timeout_seconds,
        )
        if response.status_code < 200 or response.status_code >= 300:
            raise BackendClientResponseError(f"Backend upsert failed with HTTP {response.status_code}")
        try:
            decoded = json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BackendClientJsonError("Backend upsert returned invalid JSON") from exc
        if not isinstance(decoded, Mapping):
            raise BackendClientJsonError("Backend upsert JSON response must be an object")
        try:
            return BackendUpsertResult.model_validate(decoded)
        except ValidationError as exc:
            raise BackendClientJsonError("Backend upsert JSON response did not match schema") from exc

    def _internal_key(self) -> str:
        key = self._config.internal_key
        if key is None:
            key = os.environ.get("AGENT_INTERNAL_KEY")
        if not key:
            raise BackendClientAuthError("AGENT_INTERNAL_KEY is required for backend writes")
        return key

    def _url(self, path: str) -> str:
        return f"{self._config.base_url.rstrip('/')}{path}"


class UrlLibBackendTransport:
    async def post_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        json_body: Mapping[str, JsonValue],
        timeout_seconds: float,
    ) -> BackendHttpResponse:
        return await asyncio.to_thread(
            self._post_json_sync,
            url,
            dict(headers),
            dict(json_body),
            timeout_seconds,
        )

    @staticmethod
    def _post_json_sync(
        url: str,
        headers: dict[str, str],
        json_body: dict[str, JsonValue],
        timeout_seconds: float,
    ) -> BackendHttpResponse:
        body = json.dumps(json_body, sort_keys=True, separators=(",", ":")).encode("utf-8")
        request = Request(url, data=body, headers=headers, method="POST")
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                return BackendHttpResponse(
                    status_code=int(response.status),
                    body=response.read(),
                    headers=dict(response.headers.items()),
                )
        except HTTPError as exc:
            return BackendHttpResponse(
                status_code=int(exc.code),
                body=exc.read(),
                headers=dict(exc.headers.items()),
            )
        except URLError as exc:
            raise BackendClientResponseError(f"Backend upsert transport failed: {exc.reason}") from exc
