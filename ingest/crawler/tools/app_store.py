from __future__ import annotations

from collections.abc import Mapping
from json import dumps
from math import isfinite
from typing import Protocol

from crawler.harness.checkpoint import JsonValue
from crawler.tools.http import FetchError, RawDoc


class AppStoreFetchError(FetchError):
    """Raised when app-store structured clients return invalid data."""


class GooglePlayClient(Protocol):
    async def lookup(
        self,
        app_id: str,
        *,
        country: str | None = None,
    ) -> Mapping[str, JsonValue]: ...


class AppleAppStoreClient(Protocol):
    async def lookup(
        self,
        app_id: str,
        *,
        country: str | None = None,
    ) -> Mapping[str, JsonValue]: ...


class AppStoreFetchTool:
    def __init__(
        self,
        *,
        google_play: GooglePlayClient | None = None,
        apple_app_store: AppleAppStoreClient | None = None,
    ) -> None:
        self._google_play = google_play
        self._apple_app_store = apple_app_store

    async def fetch(
        self,
        *,
        platform: str,
        app_id: str,
        country: str | None = None,
    ) -> RawDoc:
        normalized = platform.lower().replace("-", "_")
        if normalized in {"google_play", "android", "play"}:
            if self._google_play is None:
                raise RuntimeError("Google Play client is not configured")
            data = _validate_json_mapping(dict(await self._google_play.lookup(app_id, country=country)))
            source_url = f"app-store://google-play/{app_id}"
            platform_name = "google_play"
        elif normalized in {"apple_app_store", "ios", "app_store", "apple"}:
            if self._apple_app_store is None:
                raise RuntimeError("Apple App Store client is not configured")
            data = _validate_json_mapping(
                dict(await self._apple_app_store.lookup(app_id, country=country))
            )
            source_url = f"app-store://apple/{app_id}"
            platform_name = "apple_app_store"
        else:
            raise ValueError(f"Unsupported app store platform: {platform!r}")
        payload = dumps(data, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return RawDoc(
            source_url=source_url,
            content_type="application/json",
            text=payload,
            bytes=payload.encode("utf-8"),
            metadata={"platform": platform_name, "app_id": app_id, "country": country},
        )


def _validate_json_mapping(data: Mapping[str, object]) -> dict[str, JsonValue]:
    try:
        dumps(data, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise AppStoreFetchError("App-store client returned non-JSON data") from exc
    return {str(key): _validate_json_value(value) for key, value in data.items()}


def _validate_json_value(value: object) -> JsonValue:
    if isinstance(value, str | bool) or value is None:
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not isfinite(value):
            raise AppStoreFetchError("App-store client returned non-finite number")
        return value
    if isinstance(value, list):
        return [_validate_json_value(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _validate_json_value(item) for key, item in value.items()}
    raise AppStoreFetchError(f"App-store client returned unsupported value: {type(value).__name__}")
