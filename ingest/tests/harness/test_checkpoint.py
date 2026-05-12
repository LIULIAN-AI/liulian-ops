from __future__ import annotations

import asyncio
import json

import pytest

from crawler.harness.checkpoint import (
    AsyncRedisCheckpointStore,
    CheckpointCorruptionError,
    CheckpointState,
    InMemoryCheckpointStore,
    RedisCheckpointStore,
    StaleCheckpointError,
)


class FakeRedisCheckpointClient:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, name: str) -> bytes | str | None:
        return self.values.get(name)

    def eval(self, script: str, numkeys: int, *keys_and_args: str) -> int:
        key, value, sequence, state_key = keys_and_args
        existing = self.values.get(key)
        if existing is not None:
            decoded = json.loads(existing)
            if decoded["key"] != state_key:
                return -1
            if int(sequence) < int(decoded["sequence"]):
                return 0
        self.values[key] = value
        return 1

    def delete(self, name: str) -> int:
        return int(self.values.pop(name, None) is not None)


class FakeAsyncRedisCheckpointClient:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def get(self, name: str) -> bytes | str | None:
        return self.values.get(name)

    async def eval(self, script: str, numkeys: int, *keys_and_args: str) -> int:
        key, value, sequence, state_key = keys_and_args
        existing = self.values.get(key)
        if existing is not None:
            decoded = json.loads(existing)
            if decoded["key"] != state_key:
                return -1
            if int(sequence) < int(decoded["sequence"]):
                return 0
        self.values[key] = value
        return 1

    async def delete(self, name: str) -> int:
        return int(self.values.pop(name, None) is not None)


def test_in_memory_checkpoint_store_load_save_delete() -> None:
    store = InMemoryCheckpointStore()
    state = CheckpointState(key="source:bank-a", cursor="page-1", sequence=2)

    saved = store.save(state)

    assert saved.key == "source:bank-a"
    assert saved.cursor == "page-1"
    assert saved.sequence == 2
    assert store.load("source:bank-a") == saved

    store.delete("source:bank-a")
    assert store.load("source:bank-a") is None


def test_redis_checkpoint_store_serializes_pydantic_state() -> None:
    client = FakeRedisCheckpointClient()
    store = RedisCheckpointStore(client, prefix="test:checkpoint")

    saved = store.save(
        CheckpointState(
            key="crawl",
            cursor="cursor-42",
            sequence=42,
            metadata={"source": "unit", "retries": 1},
        )
    )

    assert "test:checkpoint:crawl" in client.values
    loaded = store.load("crawl")
    assert loaded == saved
    assert loaded is not None
    assert loaded.metadata["source"] == "unit"

    store.delete("crawl")
    assert store.load("crawl") is None


def test_redis_checkpoint_store_rejects_mismatched_serialized_key() -> None:
    client = FakeRedisCheckpointClient()
    client.values["test:checkpoint:requested"] = CheckpointState(key="other").model_dump_json()
    store = RedisCheckpointStore(client, prefix="test:checkpoint")

    with pytest.raises(CheckpointCorruptionError, match="requested"):
        store.load("requested")


def test_checkpoint_store_rejects_stale_sequence_saves() -> None:
    store = InMemoryCheckpointStore()
    store.save(CheckpointState(key="crawl", sequence=10))

    with pytest.raises(StaleCheckpointError, match="sequence 9"):
        store.save(CheckpointState(key="crawl", sequence=9))

    loaded = store.load("crawl")
    assert loaded is not None
    assert loaded.sequence == 10


def test_redis_checkpoint_store_atomically_rejects_stale_sequence() -> None:
    client = FakeRedisCheckpointClient()
    store = RedisCheckpointStore(client, prefix="test:checkpoint")
    store.save(CheckpointState(key="crawl", sequence=10))

    with pytest.raises(StaleCheckpointError):
        store.save(CheckpointState(key="crawl", sequence=9))

    loaded = store.load("crawl")
    assert loaded is not None
    assert loaded.sequence == 10


def test_async_redis_checkpoint_store_round_trips_and_rejects_stale_state() -> None:
    async def run() -> None:
        client = FakeAsyncRedisCheckpointClient()
        store = AsyncRedisCheckpointStore(client, prefix="test:checkpoint")

        saved = await store.save(CheckpointState(key="crawl", sequence=3))
        assert await store.load("crawl") == saved

        with pytest.raises(StaleCheckpointError):
            await store.save(CheckpointState(key="crawl", sequence=2))

        await store.delete("crawl")
        assert await store.load("crawl") is None

    asyncio.run(run())
