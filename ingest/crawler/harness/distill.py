from __future__ import annotations

import re

from crawler.harness.checkpoint import JsonValue

RAW_CONTENT_KEYS = frozenset(
    {
        "document",
        "documents",
        "raw_document",
        "raw_documents",
        "html",
        "raw_html",
        "body_html",
        "page_html",
        "content",
        "raw_content",
        "text",
        "raw_text",
    }
)


def redact_json(value: JsonValue | dict[str, JsonValue]) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        return {}
    redacted: dict[str, JsonValue] = {}
    for key, child in value.items():
        if _is_raw_content_key(key):
            redacted[key] = "[redacted]"
        elif isinstance(child, dict):
            redacted[key] = redact_json(child)
        elif isinstance(child, list):
            redacted[key] = [_redact_item(item) for item in child]
        else:
            redacted[key] = child
    return redacted


def _redact_item(value: JsonValue) -> JsonValue:
    if isinstance(value, dict):
        return redact_json(value)
    if isinstance(value, list):
        return [_redact_item(item) for item in value]
    return value


def _is_raw_content_key(key: str) -> bool:
    normalized = key.lower()
    compact = re.sub(r"[^a-z0-9]", "", normalized)
    if normalized in RAW_CONTENT_KEYS or normalized.endswith("_html") or normalized.startswith("raw_"):
        return True
    exact = {
        "bodytext",
        "extractedtext",
        "htmlcontent",
        "markdown",
        "pagecontent",
        "pagetext",
        "rawcontent",
        "rawdocument",
        "rawhtml",
        "rawtext",
    }
    if compact in exact:
        return True
    return compact.startswith("raw") and any(
        marker in compact for marker in ("content", "document", "html", "markdown", "text")
    )
