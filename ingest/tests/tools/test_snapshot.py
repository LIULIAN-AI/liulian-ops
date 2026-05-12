from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime

import pytest

from crawler.tools.snapshot import SnapshotConflictError, SnapshotStore, snapshot_key


class FakeObjectStoreClient:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, dict[str, str], str]] = {}
        self.puts: list[str] = []

    def put_object(
        self,
        *,
        Bucket: str,
        Key: str,
        Body: bytes,
        ContentType: str,
        Metadata: Mapping[str, str],
    ) -> object:
        self.objects[Key] = (Body, dict(Metadata), ContentType)
        self.puts.append(Key)
        return {"Bucket": Bucket, "Key": Key}

    def head_object(self, *, Bucket: str, Key: str) -> Mapping[str, object]:
        try:
            _body, metadata, _content_type = self.objects[Key]
        except KeyError:
            raise KeyError(Key) from None
        return {"Bucket": Bucket, "Key": Key, "Metadata": metadata}


def test_snapshot_hash_key_and_metadata_redaction() -> None:
    client = FakeObjectStoreClient()
    store = SnapshotStore(client, bucket="snapshots")
    ts = datetime(2026, 5, 5, 10, 11, 12, tzinfo=UTC)

    record = store.save(
        source="fdic",
        sort_id="bank-42",
        ts=ts,
        ext=".html",
        content="<html>secret</html>",
        content_type="text/html",
        metadata={
            "url": "https://example.test",
            "raw_content": "<html>secret</html>",
            "nested": {"bodyText": "secret", "safe": "ok"},
            "items": [{"markdown": "# secret"}],
        },
    )

    assert record.key == "fdic/bank-42/20260505T101112Z.html"
    assert record.sha256 == "85d12d6ee86194efeccda40478e57837e268e370929d59aa2aeaf07829b27496"
    assert record.size_bytes == 19
    assert record.metadata["url"] == "https://example.test"
    assert record.metadata["raw_content"] == "[redacted]"
    assert record.metadata["nested"] == {"bodyText": "[redacted]", "safe": "ok"}
    assert record.metadata["items"] == [{"markdown": "[redacted]"}]
    assert "<html>secret</html>" not in client.objects[record.key][1].values()
    assert "secret" not in str(client.objects[record.key][1])


def test_snapshot_content_hash_dedup_skips_duplicate_put() -> None:
    client = FakeObjectStoreClient()
    store = SnapshotStore(client, bucket="snapshots")
    first = store.save(
        source="fdic",
        sort_id="bank-42",
        ts=datetime(2026, 5, 5, 10, 0, tzinfo=UTC),
        ext="json",
        content=b'{"ok":true}',
        content_type="application/json",
    )
    duplicate = store.save(
        source="fdic",
        sort_id="bank-42",
        ts=datetime(2026, 5, 5, 11, 0, tzinfo=UTC),
        ext="json",
        content=b'{"ok":true}',
        content_type="application/json",
    )

    assert client.puts == [
        first.key,
        "_hash/40/4062edaf750fb8074e7e83e0c9028c94e32468a8b6f1614774328ef045150f93",
    ]
    assert duplicate.stored is False
    assert duplicate.duplicate_of == first.key
    assert duplicate.key == "fdic/bank-42/20260505T110000Z.json"


def test_snapshot_existing_key_same_hash_is_not_rewritten() -> None:
    client = FakeObjectStoreClient()
    ts = datetime(2026, 5, 5, 10, 0, tzinfo=UTC)
    key = snapshot_key(source="fdic", sort_id="bank-42", ts=ts, ext="txt")
    client.objects[key] = (
        b"same",
        {"sha256": "0967115f2813a3541eaef77de9d9d5773f1c0c04314b0bbfe4ff3b3b1c55b5d5"},
        "text/plain",
    )
    store = SnapshotStore(client, bucket="snapshots")

    record = store.save(
        source="fdic",
        sort_id="bank-42",
        ts=ts,
        ext="txt",
        content=b"same",
        content_type="text/plain",
    )

    assert client.puts == ["_hash/09/0967115f2813a3541eaef77de9d9d5773f1c0c04314b0bbfe4ff3b3b1c55b5d5"]
    assert record.stored is False
    assert record.duplicate_of == key


def test_snapshot_existing_key_different_hash_conflicts() -> None:
    client = FakeObjectStoreClient()
    ts = datetime(2026, 5, 5, 10, 0, tzinfo=UTC)
    key = snapshot_key(source="fdic", sort_id="bank-42", ts=ts, ext="txt")
    client.objects[key] = (b"old", {"sha256": "old-hash"}, "text/plain")
    store = SnapshotStore(client, bucket="snapshots")

    with pytest.raises(SnapshotConflictError):
        store.save(
            source="fdic",
            sort_id="bank-42",
            ts=ts,
            ext="txt",
            content=b"new",
            content_type="text/plain",
        )


def test_snapshot_hash_dedup_survives_new_store_instance() -> None:
    client = FakeObjectStoreClient()
    first_store = SnapshotStore(client, bucket="snapshots")
    first = first_store.save(
        source="fdic",
        sort_id="bank-42",
        ts=datetime(2026, 5, 5, 10, 0, tzinfo=UTC),
        ext="json",
        content=b'{"ok":true}',
        content_type="application/json",
    )
    second_store = SnapshotStore(client, bucket="snapshots")

    duplicate = second_store.save(
        source="fdic",
        sort_id="bank-42",
        ts=datetime(2026, 5, 5, 11, 0, tzinfo=UTC),
        ext="json",
        content=b'{"ok":true}',
        content_type="application/json",
    )

    assert duplicate.stored is False
    assert duplicate.duplicate_of == first.key
    assert duplicate.key not in client.objects


def test_snapshot_treats_s3_no_such_key_as_missing() -> None:
    class NoSuchKeyError(Exception):
        response = {"Error": {"Code": "NoSuchKey"}}

    class S3LikeClient(FakeObjectStoreClient):
        def head_object(self, *, Bucket: str, Key: str) -> Mapping[str, object]:
            raise NoSuchKeyError

    client = S3LikeClient()
    store = SnapshotStore(client, bucket="snapshots")

    record = store.save(
        source="fdic",
        sort_id="bank-42",
        ts=datetime(2026, 5, 5, 10, 0, tzinfo=UTC),
        ext="txt",
        content=b"new",
    )

    assert record.stored is True
