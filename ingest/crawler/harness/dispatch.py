from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from crawler.harness.checkpoint import JsonValue


class StreamEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_type: str = Field(min_length=1)
    payload: dict[str, JsonValue] = Field(default_factory=dict)
    dedup_key: str | None = Field(default=None, min_length=1)
    event_id: UUID = Field(default_factory=uuid4)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PublishResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    stream: str
    stream_id: str | None
    dedup_key: str | None = None
    duplicate: bool = False


class StreamProducer(Protocol):
    def publish(self, event: StreamEvent) -> PublishResult: ...


class RedisStreamsClient(Protocol):
    def xadd(self, name: str, fields: Mapping[str, str], id: str = "*") -> bytes | str: ...


class RedisDedupClient(Protocol):
    def set(
        self,
        name: str,
        value: str,
        ex: int | None = None,
        nx: bool = False,
    ) -> bool | bytes | str | None: ...

    def delete(self, name: str) -> object: ...


class AsyncRedisStreamsClient(Protocol):
    async def xadd(self, name: str, fields: Mapping[str, str], id: str = "*") -> bytes | str: ...


class AsyncRedisDedupClient(Protocol):
    async def set(
        self,
        name: str,
        value: str,
        ex: int | None = None,
        nx: bool = False,
    ) -> bool | bytes | str | None: ...

    async def delete(self, name: str) -> object: ...


class RedisStreamsProducer:
    def __init__(
        self,
        client: RedisStreamsClient,
        *,
        stream: str,
        dedup_client: RedisDedupClient | None = None,
        dedup_prefix: str = "crawler:dedup",
        dedup_ttl_seconds: int | None = None,
    ) -> None:
        self._client = client
        self._stream = stream
        self._dedup_client = dedup_client
        self._dedup_prefix = dedup_prefix.rstrip(":")
        self._dedup_ttl_seconds = dedup_ttl_seconds

    def publish(self, event: StreamEvent) -> PublishResult:
        dedup_claimed = False
        if event.dedup_key is not None and not self._claim_dedup_key(event.dedup_key):
            return PublishResult(
                stream=self._stream,
                stream_id=None,
                dedup_key=event.dedup_key,
                duplicate=True,
            )
        dedup_claimed = event.dedup_key is not None and self._dedup_client is not None
        try:
            stream_id = self._client.xadd(
                self._stream,
                _event_fields(event),
            )
        except Exception:
            if dedup_claimed and event.dedup_key is not None:
                self._release_dedup_key(event.dedup_key)
            raise
        if isinstance(stream_id, bytes):
            stream_id = stream_id.decode("utf-8")
        return PublishResult(stream=self._stream, stream_id=stream_id, dedup_key=event.dedup_key)

    def _claim_dedup_key(self, dedup_key: str) -> bool:
        if self._dedup_client is None:
            return True
        result = self._dedup_client.set(
            f"{self._dedup_prefix}:{dedup_key}",
            "1",
            ex=self._dedup_ttl_seconds,
            nx=True,
        )
        return bool(result)

    def _release_dedup_key(self, dedup_key: str) -> None:
        if self._dedup_client is not None:
            self._dedup_client.delete(f"{self._dedup_prefix}:{dedup_key}")


class AsyncRedisStreamsProducer:
    def __init__(
        self,
        client: AsyncRedisStreamsClient,
        *,
        stream: str,
        dedup_client: AsyncRedisDedupClient | None = None,
        dedup_prefix: str = "crawler:dedup",
        dedup_ttl_seconds: int | None = None,
    ) -> None:
        self._client = client
        self._stream = stream
        self._dedup_client = dedup_client
        self._dedup_prefix = dedup_prefix.rstrip(":")
        self._dedup_ttl_seconds = dedup_ttl_seconds

    async def publish(self, event: StreamEvent) -> PublishResult:
        dedup_claimed = False
        if event.dedup_key is not None and not await self._claim_dedup_key(event.dedup_key):
            return PublishResult(
                stream=self._stream,
                stream_id=None,
                dedup_key=event.dedup_key,
                duplicate=True,
            )
        dedup_claimed = event.dedup_key is not None and self._dedup_client is not None
        try:
            stream_id = await self._client.xadd(self._stream, _event_fields(event))
        except Exception:
            if dedup_claimed and event.dedup_key is not None:
                await self._release_dedup_key(event.dedup_key)
            raise
        if isinstance(stream_id, bytes):
            stream_id = stream_id.decode("utf-8")
        return PublishResult(stream=self._stream, stream_id=stream_id, dedup_key=event.dedup_key)

    async def _claim_dedup_key(self, dedup_key: str) -> bool:
        if self._dedup_client is None:
            return True
        result = await self._dedup_client.set(
            f"{self._dedup_prefix}:{dedup_key}",
            "1",
            ex=self._dedup_ttl_seconds,
            nx=True,
        )
        return bool(result)

    async def _release_dedup_key(self, dedup_key: str) -> None:
        if self._dedup_client is not None:
            await self._dedup_client.delete(f"{self._dedup_prefix}:{dedup_key}")


class InMemoryStreamProducer:
    def __init__(self, *, stream: str = "crawler:test") -> None:
        self._stream = stream
        self._events: list[StreamEvent] = []
        self._dedup_keys: set[str] = set()

    @property
    def events(self) -> tuple[StreamEvent, ...]:
        return tuple(self._events)

    def publish(self, event: StreamEvent) -> PublishResult:
        if event.dedup_key is not None:
            if event.dedup_key in self._dedup_keys:
                return PublishResult(
                    stream=self._stream,
                    stream_id=None,
                    dedup_key=event.dedup_key,
                    duplicate=True,
                )
            self._dedup_keys.add(event.dedup_key)
        self._events.append(event)
        return PublishResult(
            stream=self._stream,
            stream_id=f"memory-{len(self._events)}",
            dedup_key=event.dedup_key,
        )


def _event_fields(event: StreamEvent) -> dict[str, str]:
    return {
        "event": event.model_dump_json(),
        "event_type": event.event_type,
        "event_id": str(event.event_id),
    }
