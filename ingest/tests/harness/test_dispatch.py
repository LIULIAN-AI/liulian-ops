from __future__ import annotations

import asyncio
from collections.abc import Mapping

import pytest

from crawler.harness.dispatch import (
    AsyncRedisStreamsProducer,
    InMemoryStreamProducer,
    RedisStreamsProducer,
    StreamEvent,
)


class FakeRedisStreamsClient:
    def __init__(self, *, fail_xadd: bool = False) -> None:
        self.added: list[tuple[str, Mapping[str, str]]] = []
        self.fail_xadd = fail_xadd

    def xadd(self, name: str, fields: Mapping[str, str], id: str = "*") -> str:
        if self.fail_xadd:
            raise TimeoutError("stream unavailable")
        self.added.append((name, fields))
        return f"0-{len(self.added)}"


class FakeRedisDedupClient:
    def __init__(self) -> None:
        self.keys: set[str] = set()

    def set(
        self,
        name: str,
        value: str,
        ex: int | None = None,
        nx: bool = False,
    ) -> bool:
        if nx and name in self.keys:
            return False
        self.keys.add(name)
        return True

    def delete(self, name: str) -> int:
        existed = name in self.keys
        self.keys.discard(name)
        return int(existed)


class FakeAsyncRedisStreamsClient:
    def __init__(self) -> None:
        self.added: list[tuple[str, Mapping[str, str]]] = []

    async def xadd(self, name: str, fields: Mapping[str, str], id: str = "*") -> bytes:
        self.added.append((name, fields))
        return b"0-1"


class FakeAsyncRedisDedupClient:
    def __init__(self) -> None:
        self.keys: set[str] = set()

    async def set(
        self,
        name: str,
        value: str,
        ex: int | None = None,
        nx: bool = False,
    ) -> bool:
        if nx and name in self.keys:
            return False
        self.keys.add(name)
        return True

    async def delete(self, name: str) -> int:
        existed = name in self.keys
        self.keys.discard(name)
        return int(existed)


def test_in_memory_stream_producer_publishes_and_deduplicates() -> None:
    producer = InMemoryStreamProducer(stream="test-stream")
    event = StreamEvent(
        event_type="crawl.requested",
        payload={"source": "unit"},
        dedup_key="source:unit",
    )

    first = producer.publish(event)
    second = producer.publish(event)

    assert first.stream_id == "memory-1"
    assert first.duplicate is False
    assert second.stream_id is None
    assert second.duplicate is True
    assert producer.events == (event,)


def test_redis_streams_producer_uses_xadd_and_json_envelope() -> None:
    client = FakeRedisStreamsClient()
    producer = RedisStreamsProducer(client, stream="crawler:events")

    result = producer.publish(StreamEvent(event_type="crawl.done", payload={"count": 3}))

    assert result.stream == "crawler:events"
    assert result.stream_id == "0-1"
    assert client.added[0][0] == "crawler:events"
    fields = client.added[0][1]
    assert fields["event_type"] == "crawl.done"
    assert '"count":3' in fields["event"]


def test_redis_streams_producer_uses_dedup_key_before_xadd() -> None:
    client = FakeRedisStreamsClient()
    dedup = FakeRedisDedupClient()
    producer = RedisStreamsProducer(
        client,
        stream="crawler:events",
        dedup_client=dedup,
        dedup_ttl_seconds=60,
    )
    event = StreamEvent(event_type="crawl.requested", dedup_key="bank:1")

    first = producer.publish(event)
    second = producer.publish(event)

    assert first.duplicate is False
    assert second.duplicate is True
    assert len(client.added) == 1
    assert "crawler:dedup:bank:1" in dedup.keys


def test_redis_streams_producer_releases_dedup_key_when_xadd_fails() -> None:
    client = FakeRedisStreamsClient(fail_xadd=True)
    dedup = FakeRedisDedupClient()
    producer = RedisStreamsProducer(client, stream="crawler:events", dedup_client=dedup)

    with pytest.raises(TimeoutError):
        producer.publish(StreamEvent(event_type="crawl.requested", dedup_key="bank:1"))

    assert "crawler:dedup:bank:1" not in dedup.keys


def test_async_redis_streams_producer_awaits_xadd_and_dedup() -> None:
    async def run() -> None:
        client = FakeAsyncRedisStreamsClient()
        dedup = FakeAsyncRedisDedupClient()
        producer = AsyncRedisStreamsProducer(
            client,
            stream="crawler:events",
            dedup_client=dedup,
        )
        event = StreamEvent(event_type="crawl.requested", dedup_key="bank:1")

        first = await producer.publish(event)
        second = await producer.publish(event)

        assert first.stream_id == "0-1"
        assert second.duplicate is True
        assert len(client.added) == 1

    asyncio.run(run())
