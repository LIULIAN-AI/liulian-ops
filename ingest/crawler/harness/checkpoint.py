from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

type JsonValue = str | int | float | bool | None | list[JsonValue] | dict[str, JsonValue]

_SAVE_SCRIPT = """
local existing = redis.call("GET", KEYS[1])
if existing then
  local decoded = cjson.decode(existing)
  if decoded["key"] ~= ARGV[3] then
    return -1
  end
  local existing_sequence = tonumber(decoded["sequence"]) or 0
  if tonumber(ARGV[2]) < existing_sequence then
    return 0
  end
end
redis.call("SET", KEYS[1], ARGV[1])
return 1
"""


class CheckpointState(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str = Field(min_length=1)
    cursor: str | None = None
    sequence: int = Field(default=0, ge=0)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class CheckpointCorruptionError(RuntimeError):
    """Raised when persisted checkpoint data does not match its storage key."""


class StaleCheckpointError(RuntimeError):
    """Raised when a checkpoint save would move sequence state backwards."""


class CheckpointStore(Protocol):
    def load(self, key: str) -> CheckpointState | None: ...

    def save(self, state: CheckpointState) -> CheckpointState: ...

    def delete(self, key: str) -> None: ...


class InMemoryCheckpointStore:
    def __init__(self) -> None:
        self._states: dict[str, CheckpointState] = {}

    def load(self, key: str) -> CheckpointState | None:
        return self._states.get(key)

    def save(self, state: CheckpointState) -> CheckpointState:
        existing = self._states.get(state.key)
        _raise_if_stale(existing, state)
        saved = state.model_copy(update={"updated_at": datetime.now(UTC)})
        self._states[state.key] = saved
        return saved

    def delete(self, key: str) -> None:
        self._states.pop(key, None)


class RedisCheckpointClient(Protocol):
    def get(self, name: str) -> bytes | str | None: ...

    def eval(self, script: str, numkeys: int, *keys_and_args: str) -> int | bytes | str: ...

    def delete(self, name: str) -> object: ...


class AsyncRedisCheckpointClient(Protocol):
    async def get(self, name: str) -> bytes | str | None: ...

    async def eval(self, script: str, numkeys: int, *keys_and_args: str) -> int | bytes | str: ...

    async def delete(self, name: str) -> object: ...


class RedisCheckpointStore:
    def __init__(self, client: RedisCheckpointClient, *, prefix: str = "crawler:checkpoint") -> None:
        self._client = client
        self._prefix = prefix.rstrip(":")

    def load(self, key: str) -> CheckpointState | None:
        raw = self._client.get(self._redis_key(key))
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        state = CheckpointState.model_validate_json(raw)
        _raise_if_key_mismatch(key, state)
        return state

    def save(self, state: CheckpointState) -> CheckpointState:
        saved = state.model_copy(update={"updated_at": datetime.now(UTC)})
        result = self._client.eval(
            _SAVE_SCRIPT,
            1,
            self._redis_key(saved.key),
            saved.model_dump_json(),
            str(saved.sequence),
            saved.key,
        )
        _raise_if_save_rejected(result, saved)
        return saved

    def delete(self, key: str) -> None:
        self._client.delete(self._redis_key(key))

    def _redis_key(self, key: str) -> str:
        return f"{self._prefix}:{key}"


class AsyncRedisCheckpointStore:
    def __init__(self, client: AsyncRedisCheckpointClient, *, prefix: str = "crawler:checkpoint") -> None:
        self._client = client
        self._prefix = prefix.rstrip(":")

    async def load(self, key: str) -> CheckpointState | None:
        raw = await self._client.get(self._redis_key(key))
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        state = CheckpointState.model_validate_json(raw)
        _raise_if_key_mismatch(key, state)
        return state

    async def save(self, state: CheckpointState) -> CheckpointState:
        saved = state.model_copy(update={"updated_at": datetime.now(UTC)})
        result = await self._client.eval(
            _SAVE_SCRIPT,
            1,
            self._redis_key(saved.key),
            saved.model_dump_json(),
            str(saved.sequence),
            saved.key,
        )
        _raise_if_save_rejected(result, saved)
        return saved

    async def delete(self, key: str) -> None:
        await self._client.delete(self._redis_key(key))

    def _redis_key(self, key: str) -> str:
        return f"{self._prefix}:{key}"


def _raise_if_key_mismatch(requested_key: str, state: CheckpointState) -> None:
    if state.key != requested_key:
        raise CheckpointCorruptionError(
            f"Checkpoint key mismatch: requested {requested_key!r}, found {state.key!r}"
        )


def _raise_if_stale(existing: CheckpointState | None, incoming: CheckpointState) -> None:
    if existing is not None and incoming.sequence < existing.sequence:
        raise StaleCheckpointError(
            f"Checkpoint {incoming.key!r} sequence {incoming.sequence} is older "
            f"than existing sequence {existing.sequence}"
        )


def _raise_if_save_rejected(result: int | bytes | str, state: CheckpointState) -> None:
    if isinstance(result, bytes):
        result = result.decode("utf-8")
    code = int(result)
    if code == -1:
        raise CheckpointCorruptionError(f"Checkpoint key mismatch while saving {state.key!r}")
    if code == 0:
        raise StaleCheckpointError(f"Checkpoint {state.key!r} sequence {state.sequence} is stale")
