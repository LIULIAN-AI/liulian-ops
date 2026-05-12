from __future__ import annotations

from builtins import bytes as Bytes
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from hashlib import sha256
from inspect import isawaitable
from typing import Any, Protocol
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field

from crawler.harness.checkpoint import JsonValue


class FetchError(RuntimeError):
    """Base error for offline-injectable fetch tools."""


class FetchBlockedError(FetchError):
    """Raised when a fetch is blocked by local allow-list policy."""


class FetchTransportError(FetchError):
    """Raised when an injected transport cannot fetch a document."""


class FetchSizeError(FetchError):
    """Raised when a response exceeds the configured in-memory fetch boundary."""


class RawDoc(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_url: str = Field(min_length=1)
    content_type: str = Field(default="application/octet-stream", min_length=1)
    text: str | None = None
    bytes: Bytes | None = None
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    content_hash: str = ""

    def model_post_init(self, __context: Any) -> None:
        _ = __context
        if self.text is None and self.bytes is None:
            raise ValueError("RawDoc requires text or bytes")
        text = self.text
        material = self.bytes if self.bytes is not None else (text or "").encode("utf-8")
        digest = sha256(material).hexdigest()
        if self.content_hash and self.content_hash != digest:
            raise ValueError("RawDoc content_hash does not match content")
        object.__setattr__(self, "content_hash", digest)


class HttpResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    url: str = Field(min_length=1)
    status_code: int = Field(ge=100, le=599)
    headers: dict[str, str] = Field(default_factory=dict)
    body: bytes = b""


class AsyncHttpTransport(Protocol):
    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> HttpResponse: ...


RetryPredicate = Callable[[HttpResponse | BaseException], bool]
AsyncSleep = Callable[[float], Awaitable[None] | None]


class RetryPolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    attempts: int = Field(default=3, ge=1)
    backoff_seconds: tuple[float, ...] = (0.0, 0.05, 0.2)
    retry_statuses: frozenset[int] = frozenset({429, 500, 502, 503, 504})

    def delay_for_attempt(self, attempt_index: int) -> float:
        if not self.backoff_seconds:
            return 0.0
        index = min(attempt_index, len(self.backoff_seconds) - 1)
        return self.backoff_seconds[index]


class HttpFetchTool:
    def __init__(
        self,
        transport: AsyncHttpTransport,
        *,
        allowed_hosts: Sequence[str] = (),
        retry_policy: RetryPolicy | None = None,
        retry_predicate: RetryPredicate | None = None,
        sleep: AsyncSleep | None = None,
        user_agent: str = "neobanker-crawler/0.1 (+respectful-offline-fetch)",
        default_headers: Mapping[str, str] | None = None,
        max_bytes: int = 5_000_000,
    ) -> None:
        self._transport = transport
        self._allowed_hosts = frozenset(host.lower() for host in allowed_hosts)
        self._retry_policy = retry_policy or RetryPolicy()
        self._retry_predicate = retry_predicate
        self._sleep = sleep or _noop_sleep
        self._max_bytes = max_bytes
        self._default_headers = {
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,application/json,text/plain,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "DNT": "1",
        }
        if default_headers:
            self._default_headers.update(default_headers)

    async def fetch(
        self,
        url: str,
        *,
        method: str = "GET",
        headers: Mapping[str, str] | None = None,
        timeout_seconds: float = 15.0,
    ) -> RawDoc:
        self._validate_url(url)
        request_headers = {**self._default_headers, **dict(headers or {})}
        response = await self._request_with_retries(
            method=method,
            url=url,
            headers=request_headers,
            timeout_seconds=timeout_seconds,
        )
        if response.status_code >= 400:
            raise FetchTransportError(f"HTTP {response.status_code} fetching {url!r}")
        self._validate_url(response.url)
        if len(response.body) > self._max_bytes:
            raise FetchSizeError(f"Response from {response.url!r} exceeds {self._max_bytes} bytes")
        content_type = _header(response.headers, "content-type") or "application/octet-stream"
        text = _decode_text(response.body, content_type)
        respectful_headers: list[JsonValue] = []
        respectful_headers.extend(sorted(request_headers))
        return RawDoc(
            source_url=response.url,
            content_type=content_type.split(";", 1)[0].strip() or content_type,
            text=text,
            bytes=None if text is not None else response.body,
            metadata={
                "status_code": response.status_code,
                "final_url": response.url,
                "respectful_headers": respectful_headers,
            },
        )

    async def _request_with_retries(
        self,
        *,
        method: str,
        url: str,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> HttpResponse:
        last_error: BaseException | None = None
        for attempt in range(self._retry_policy.attempts):
            try:
                response = await self._transport.request(
                    method,
                    url,
                    headers=headers,
                    timeout_seconds=timeout_seconds,
                )
            except Exception as exc:
                last_error = exc
                if not self._should_retry(exc) or attempt == self._retry_policy.attempts - 1:
                    raise FetchTransportError(f"Transport failed fetching {url!r}") from exc
            else:
                if (
                    not self._should_retry(response)
                    or attempt == self._retry_policy.attempts - 1
                ):
                    return response
            await _maybe_sleep(self._sleep, self._retry_policy.delay_for_attempt(attempt))
        raise FetchTransportError(f"Transport failed fetching {url!r}") from last_error

    def _should_retry(self, value: HttpResponse | BaseException) -> bool:
        if self._retry_predicate is not None:
            return self._retry_predicate(value)
        if isinstance(value, BaseException):
            return True
        return value.status_code in self._retry_policy.retry_statuses

    def _validate_url(self, url: str) -> None:
        validate_fetch_url(url, allowed_hosts=self._allowed_hosts)


def validate_fetch_url(url: str, *, allowed_hosts: Iterable[str] = ()) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise FetchBlockedError(f"Only http(s) URLs are fetchable: {url!r}")
    host = (parsed.hostname or "").lower()
    allowed = frozenset(item.lower() for item in allowed_hosts)
    if allowed and host not in allowed:
        raise FetchBlockedError(f"Host {host!r} is not in the fetch allow-list")


async def _noop_sleep(seconds: float) -> None:
    _ = seconds


async def _maybe_sleep(sleep: AsyncSleep, seconds: float) -> None:
    result = sleep(seconds)
    if isawaitable(result):
        await result


def _header(headers: Mapping[str, str], name: str) -> str | None:
    lowered = name.lower()
    for key, value in headers.items():
        if key.lower() == lowered:
            return value
    return None


def _decode_text(body: bytes, content_type: str) -> str | None:
    normalized = content_type.lower()
    if not any(marker in normalized for marker in ("text/", "json", "xml", "html")):
        return None
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        return body.decode("utf-8", errors="replace")
