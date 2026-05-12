from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from crawler.harness.checkpoint import JsonValue
from crawler.tools.http import FetchError, RawDoc


class PdfParseError(FetchError):
    """Raised when an injected PDF parser cannot extract text."""


class PdfParseResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    text: str
    pages: int = Field(ge=0)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class PdfParser(Protocol):
    async def parse(self, data: bytes, *, source_url: str) -> PdfParseResult: ...


class PdfExtractionTool:
    def __init__(self, parser: PdfParser, *, max_chars: int = 5_000_000) -> None:
        self._parser = parser
        self._max_chars = max_chars

    async def extract(self, document: RawDoc) -> RawDoc:
        if document.bytes is None:
            raise PdfParseError("PDF extraction requires bytes")
        try:
            parsed = await self._parser.parse(document.bytes, source_url=document.source_url)
        except Exception as exc:
            raise PdfParseError(f"Could not parse PDF from {document.source_url!r}") from exc
        if len(parsed.text) > self._max_chars:
            raise PdfParseError(f"PDF text from {document.source_url!r} exceeds {self._max_chars} chars")
        return RawDoc(
            source_url=document.source_url,
            content_type="text/plain",
            text=parsed.text,
            metadata={
                **parsed.metadata,
                "pages": parsed.pages,
                "source_content_type": document.content_type,
                "source_content_hash": document.content_hash,
            },
        )
