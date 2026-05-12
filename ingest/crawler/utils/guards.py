"""Canonical raw-content guards shared across all pipeline stages."""
from __future__ import annotations

from crawler.harness.checkpoint import JsonValue

# Max character length before a string value is considered raw source content.
RAW_VALUE_MAX_LEN = 800

# Compact (alphanumeric-only) key names that indicate raw source content.
_RAW_COMPACT_KEYS = frozenset(
    {
        "bodyhtml",
        "chainofthought",
        "html",
        "htmlcontent",
        "markdown",
        "pagetext",
        "rawcontent",
        "rawdocument",
        "rawhtml",
        "rawtext",
        "reasoning",
        "sourcecontent",
    }
)


def is_raw_key(key: str) -> bool:
    """Return True if *key* names a raw-source or chain-of-thought field."""
    lowered = key.lower()
    compact = "".join(c for c in lowered if c.isalnum())
    return (
        lowered.startswith("raw_")
        or lowered.endswith("_html")
        or compact in _RAW_COMPACT_KEYS
    )


def looks_like_raw_value(value: JsonValue) -> bool:
    """Return True if *value* appears to contain raw HTML, markdown, or oversized text."""
    if isinstance(value, str):
        lowered = value.lower()
        if (
            "<html" in lowered
            or "</body" in lowered
            or "<script" in lowered
            or "<div" in lowered
        ):
            return True
        return len(value) > RAW_VALUE_MAX_LEN
    if isinstance(value, dict):
        return any(
            is_raw_key(key) or looks_like_raw_value(child)
            for key, child in value.items()
        )
    if isinstance(value, list):
        return any(looks_like_raw_value(child) for child in value)
    return False
