from __future__ import annotations

from decimal import Decimal
from importlib.resources import files
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from crawler.extractor.router import ExtractionConfigError
from crawler.tools.http import RawDoc

DEFAULT_MAX_CONTENT_CHARS = 12_000
DEFAULT_SYSTEM_PROMPT = "You extract grounded company facts from one source document."


# ---------------------------------------------------------------------------
# Extraction schemas — one per field_group in data_dictionary.yaml
# ---------------------------------------------------------------------------

class AboutExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    company_name: str | None = None
    description: str | None = None
    headquarters: str | None = None
    founded_year: int | None = None
    confidence: float = Field(..., ge=0.0, le=1.0)


class ProductExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    account_types: list[str] | None = None
    cards: list[str] | None = None
    lending: list[str] | None = None
    supported_platforms: list[str] | None = None
    confidence: float = Field(..., ge=0.0, le=1.0)


class FinancialsExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    funding_total_usd: Decimal | None = None
    assets_usd: Decimal | None = None
    revenue_usd: Decimal | None = None
    regulatory_status: str | None = None
    confidence: float = Field(..., ge=0.0, le=1.0)


class StaffExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    founders: list[str] | None = None
    executives: list[str] | None = None
    employee_count: int | None = None
    confidence: float = Field(..., ge=0.0, le=1.0)


class TechExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    mobile_apps: list[str] | None = None
    api_surface: list[str] | None = None
    security_features: list[str] | None = None
    confidence: float = Field(..., ge=0.0, le=1.0)


class MarketingExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    tagline: str | None = None
    target_segments: list[str] | None = None
    brand_claims: list[str] | None = None
    confidence: float = Field(..., ge=0.0, le=1.0)


class MarketingAppExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    app_rating: float | None = None
    review_count: int | None = None
    release_notes: str | None = None
    confidence: float = Field(..., ge=0.0, le=1.0)


class MarketingWebExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    homepage_claims: list[str] | None = None
    seo_title: str | None = None
    confidence: float = Field(..., ge=0.0, le=1.0)


class Web3Extraction(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    crypto_products: list[str] | None = None
    custody_model: str | None = None
    token_support: list[str] | None = None
    confidence: float = Field(..., ge=0.0, le=1.0)


FIELD_GROUP_SCHEMAS: dict[str, type[BaseModel]] = {
    "about": AboutExtraction,
    "product": ProductExtraction,
    "financials": FinancialsExtraction,
    "staff": StaffExtraction,
    "tech": TechExtraction,
    "marketing": MarketingExtraction,
    "marketing.app": MarketingAppExtraction,
    "marketing.web": MarketingWebExtraction,
    "web3": Web3Extraction,
}


class ExtractionPrompt(BaseModel):
    model_config = ConfigDict(frozen=True)

    field_group: str
    source_url: str
    content_hash: str
    text: str
    truncated: bool
    original_length: int
    max_content_chars: int


def schema_for_field_group(field_group: str) -> type[BaseModel]:
    try:
        return FIELD_GROUP_SCHEMAS[field_group]
    except KeyError as exc:
        raise ExtractionConfigError(f"Unsupported field_group: {field_group!r}") from exc


def build_prompt(
    *,
    field_group: str,
    document: RawDoc,
    max_content_chars: int = DEFAULT_MAX_CONTENT_CHARS,
) -> ExtractionPrompt:
    if field_group not in FIELD_GROUP_SCHEMAS:
        raise ExtractionConfigError(f"Unsupported field_group: {field_group!r}")
    text = _document_text(document)
    truncated_text, truncated = _truncate_at_boundary(text, max_content_chars)
    return ExtractionPrompt(
        field_group=field_group,
        source_url=document.source_url,
        content_hash=document.content_hash,
        text=_render_prompt(
            field_group=field_group,
            source_url=document.source_url,
            content_hash=document.content_hash,
            content=truncated_text,
            truncated=truncated,
        ),
        truncated=truncated,
        original_length=len(text),
        max_content_chars=max_content_chars,
    )


def _document_text(document: RawDoc) -> str:
    if document.text is not None:
        return document.text
    if document.bytes is not None:
        return document.bytes.decode("utf-8", errors="replace")
    return ""


def _truncate_at_boundary(text: str, max_chars: int) -> tuple[str, bool]:
    if max_chars < 1:
        raise ExtractionConfigError("max_content_chars must be positive")
    if len(text) <= max_chars:
        return text, False
    window = text[:max_chars]
    boundary = max(window.rfind("\n\n"), window.rfind("\n"), window.rfind(". "), window.rfind(" "))
    if boundary < max_chars // 2:
        boundary = max_chars
    return window[:boundary].rstrip(), True


def _render_prompt(
    *,
    field_group: str,
    source_url: str,
    content_hash: str,
    content: str,
    truncated: bool,
) -> str:
    template = _about_template() if field_group == "about" else _generic_template()
    replacements = {
        "{{ source_url }}": source_url,
        "{{ content_hash }}": content_hash,
        "{{ truncated }}": "true" if truncated else "false",
        "{{ content }}": content,
        "{{ field_group }}": field_group,
    }
    for needle, value in replacements.items():
        template = template.replace(needle, value)
    return template


def _about_template() -> str:
    resource = files("crawler.extractor.prompts").joinpath("about.txt")
    return resource.read_text(encoding="utf-8")


def _generic_template() -> str:
    resource = files("crawler.extractor.prompts").joinpath("field_group.txt")
    return resource.read_text(encoding="utf-8")


def model_to_json_dict(value: BaseModel) -> dict[str, Any]:
    dumped = value.model_dump(mode="json")
    if not isinstance(dumped, dict):
        raise ExtractionConfigError("Extraction model did not dump to a JSON object")
    return dumped
