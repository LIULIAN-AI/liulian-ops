from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from hashlib import sha256
from json import dumps
from pathlib import PurePosixPath
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from crawler.harness.checkpoint import JsonValue
from crawler.harness.distill import redact_json


class SnapshotError(RuntimeError):
    """Base class for snapshot storage failures."""


class SnapshotConflictError(SnapshotError):
    """Raised when a deterministic snapshot key already stores different content."""


class ObjectStoreClient(Protocol):
    def put_object(
        self,
        *,
        Bucket: str,
        Key: str,
        Body: bytes,
        ContentType: str,
        Metadata: Mapping[str, str],
    ) -> object: ...

    def head_object(self, *, Bucket: str, Key: str) -> Mapping[str, object]: ...


class SnapshotRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    bucket: str = Field(min_length=1)
    key: str = Field(min_length=1)
    source: str = Field(min_length=1)
    sort_id: str = Field(min_length=1)
    ts: datetime
    ext: str = Field(min_length=1)
    content_type: str = Field(min_length=1)
    sha256: str = Field(min_length=64, max_length=64)
    size_bytes: int = Field(ge=0)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    duplicate_of: str | None = None
    stored: bool = True

    @field_validator("ts")
    @classmethod
    def _normalize_ts(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class SnapshotStore:
    def __init__(self, client: ObjectStoreClient, *, bucket: str) -> None:
        self._client = client
        self._bucket = bucket
        self._hash_index: dict[str, str] = {}

    def save(
        self,
        *,
        source: str,
        sort_id: str,
        ts: datetime,
        ext: str,
        content: bytes | str,
        content_type: str = "application/octet-stream",
        metadata: Mapping[str, JsonValue] | None = None,
    ) -> SnapshotRecord:
        body = content.encode("utf-8") if isinstance(content, str) else content
        digest = sha256(body).hexdigest()
        normalized_ts = _normalize_datetime(ts)
        normalized_ext = _normalize_ext(ext)
        key = snapshot_key(source=source, sort_id=sort_id, ts=normalized_ts, ext=normalized_ext)
        safe_metadata = _snapshot_metadata(
            source=source,
            sort_id=sort_id,
            ts=normalized_ts,
            ext=normalized_ext,
            content_type=content_type,
            digest=digest,
            size_bytes=len(body),
            user_metadata=metadata,
        )

        existing_for_hash = self._hash_index.get(digest)
        if existing_for_hash is None:
            existing_for_hash = self._hash_index_lookup(digest)
        if existing_for_hash is not None:
            self._hash_index[digest] = existing_for_hash
            return _record(
                bucket=self._bucket,
                key=key,
                source=source,
                sort_id=sort_id,
                ts=normalized_ts,
                ext=normalized_ext,
                content_type=content_type,
                digest=digest,
                size_bytes=len(body),
                metadata=safe_metadata,
                duplicate_of=existing_for_hash,
                stored=False,
            )

        existing = _head_object_or_none(self._client, bucket=self._bucket, key=key)
        existing_hash = _metadata_value(existing, "sha256")
        if existing_hash == digest:
            self._hash_index[digest] = key
            self._write_hash_index(digest, key)
            return _record(
                bucket=self._bucket,
                key=key,
                source=source,
                sort_id=sort_id,
                ts=normalized_ts,
                ext=normalized_ext,
                content_type=content_type,
                digest=digest,
                size_bytes=len(body),
                metadata=safe_metadata,
                duplicate_of=key,
                stored=False,
            )
        if existing_hash is not None:
            raise SnapshotConflictError(f"Snapshot key {key!r} already stores different content")

        self._client.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=body,
            ContentType=content_type,
            Metadata=_metadata_strings(safe_metadata),
        )
        self._hash_index[digest] = key
        self._write_hash_index(digest, key)
        return _record(
            bucket=self._bucket,
            key=key,
            source=source,
            sort_id=sort_id,
            ts=normalized_ts,
            ext=normalized_ext,
            content_type=content_type,
            digest=digest,
            size_bytes=len(body),
            metadata=safe_metadata,
        )

    def _hash_index_lookup(self, digest: str) -> str | None:
        existing = _head_object_or_none(
            self._client,
            bucket=self._bucket,
            key=_hash_index_key(digest),
        )
        return _metadata_value(existing, "snapshot_key")

    def _write_hash_index(self, digest: str, snapshot_key_value: str) -> None:
        self._client.put_object(
            Bucket=self._bucket,
            Key=_hash_index_key(digest),
            Body=b"",
            ContentType="application/x-neobanker-snapshot-index",
            Metadata={"sha256": digest, "snapshot_key": snapshot_key_value},
        )


def snapshot_key(*, source: str, sort_id: str, ts: datetime, ext: str) -> str:
    safe_source = _safe_segment(source)
    safe_sort_id = _safe_segment(sort_id)
    return str(PurePosixPath(safe_source, safe_sort_id, f"{_format_ts(ts)}.{_normalize_ext(ext)}"))


def _record(
    *,
    bucket: str,
    key: str,
    source: str,
    sort_id: str,
    ts: datetime,
    ext: str,
    content_type: str,
    digest: str,
    size_bytes: int,
    metadata: dict[str, JsonValue],
    duplicate_of: str | None = None,
    stored: bool = True,
) -> SnapshotRecord:
    return SnapshotRecord(
        bucket=bucket,
        key=key,
        source=source,
        sort_id=sort_id,
        ts=ts,
        ext=ext,
        content_type=content_type,
        sha256=digest,
        size_bytes=size_bytes,
        metadata=metadata,
        duplicate_of=duplicate_of,
        stored=stored,
    )


def _snapshot_metadata(
    *,
    source: str,
    sort_id: str,
    ts: datetime,
    ext: str,
    content_type: str,
    digest: str,
    size_bytes: int,
    user_metadata: Mapping[str, JsonValue] | None,
) -> dict[str, JsonValue]:
    metadata: dict[str, JsonValue] = {
        "source": source,
        "sort_id": sort_id,
        "ts": _format_ts(ts),
        "ext": ext,
        "content_type": content_type,
        "sha256": digest,
        "size_bytes": size_bytes,
    }
    metadata.update(redact_json(dict(user_metadata or {})))
    return metadata


def _head_object_or_none(
    client: ObjectStoreClient,
    *,
    bucket: str,
    key: str,
) -> Mapping[str, object] | None:
    try:
        return client.head_object(Bucket=bucket, Key=key)
    except (KeyError, FileNotFoundError):
        return None
    except Exception as exc:
        if _is_not_found_error(exc):
            return None
        raise


def _metadata_value(head: Mapping[str, object] | None, name: str) -> str | None:
    if head is None:
        return None
    raw_metadata = head.get("Metadata")
    if not isinstance(raw_metadata, Mapping):
        return None
    value = raw_metadata.get(name)
    return str(value) if value is not None else None


def _metadata_strings(metadata: Mapping[str, JsonValue]) -> dict[str, str]:
    strings: dict[str, str] = {}
    for key, value in metadata.items():
        if isinstance(value, str):
            strings[key] = value
        else:
            strings[key] = dumps(value, sort_keys=True, separators=(",", ":"))
    return strings


def _is_not_found_error(exc: Exception) -> bool:
    response = getattr(exc, "response", None)
    if isinstance(response, Mapping):
        error = response.get("Error")
        if isinstance(error, Mapping):
            code = str(error.get("Code", ""))
            if code in {"404", "NoSuchKey", "NotFound", "Not Found"}:
                return True
        status = response.get("ResponseMetadata")
        if isinstance(status, Mapping) and status.get("HTTPStatusCode") == 404:
            return True
    code_attr = str(getattr(exc, "code", ""))
    return code_attr in {"NoSuchKey", "NoSuchBucket", "NotFound"}


def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _format_ts(value: datetime) -> str:
    return _normalize_datetime(value).strftime("%Y%m%dT%H%M%SZ")


def _normalize_ext(ext: str) -> str:
    clean = ext.strip().lstrip(".").lower()
    if not clean or "/" in clean:
        raise ValueError("Snapshot extension must be a non-empty file extension")
    return clean


def _safe_segment(value: str) -> str:
    clean = value.strip().strip("/")
    if not clean or clean in {".", ".."} or "/" in clean:
        raise ValueError(f"Unsafe snapshot key segment: {value!r}")
    return clean


def _hash_index_key(digest: str) -> str:
    return str(PurePosixPath("_hash", digest[:2], digest))
