from __future__ import annotations

from collections.abc import Iterable

from crawler.harness.dispatch import PublishResult, StreamEvent


class InMemoryRunRegistry:
    def __init__(self) -> None:
        self._records: list[tuple[StreamEvent, PublishResult]] = []

    def record(self, event: StreamEvent, result: PublishResult) -> None:
        self._records.append((event, result))

    def latest(self, *, limit: int) -> tuple[tuple[StreamEvent, PublishResult], ...]:
        return tuple(self._records[-limit:])

    def find(self, *, dedup_key: str | None) -> tuple[StreamEvent, PublishResult] | None:
        for event, result in reversed(self._records):
            if event.dedup_key == dedup_key:
                return event, result
        return None

    @classmethod
    def from_events(
        cls,
        events: Iterable[StreamEvent],
        *,
        stream: str,
    ) -> InMemoryRunRegistry:
        registry = cls()
        for index, event in enumerate(events, start=1):
            registry.record(
                event,
                PublishResult(
                    stream=stream,
                    stream_id=f"memory-{index}",
                    dedup_key=event.dedup_key,
                ),
            )
        return registry
