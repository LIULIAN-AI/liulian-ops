from datetime import datetime
from uuid import UUID

from pydantic import Field

from crawler.schemas._base import _Base


class FieldProvenance(_Base):
    company_sort_id: str
    field_name: str
    source_url: str
    snapshot_uri: str
    run_id: UUID
    llm_model: str | None
    confidence: float = Field(..., ge=0.0, le=1.0)
    extracted_at: datetime
    prompt_sha: str | None


__all__ = ("FieldProvenance",)
