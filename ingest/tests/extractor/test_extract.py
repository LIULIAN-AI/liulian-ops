from __future__ import annotations

import pytest

from crawler.extractor.extract import build_prompt
from crawler.extractor.router import ExtractionConfigError
from crawler.tools.http import RawDoc


def test_about_prompt_contains_provenance_and_not_guess_instruction() -> None:
    document = RawDoc(
        source_url="https://example.test/about",
        content_type="text/html",
        text="Neo Bank is a digital bank.",
    )

    prompt = build_prompt(field_group="about", document=document)

    assert "https://example.test/about" in prompt.text
    assert document.content_hash in prompt.text
    assert "Do not guess" in prompt.text
    assert "untrusted data" in prompt.text
    assert "Untrusted source document begins" in prompt.text
    assert "Neo Bank is a digital bank." in prompt.text
    assert prompt.truncated is False


def test_long_document_truncates_at_boundary() -> None:
    document = RawDoc(
        source_url="https://example.test/about",
        content_type="text/plain",
        text="first sentence. second sentence. third sentence.",
    )

    prompt = build_prompt(field_group="about", document=document, max_content_chars=25)

    assert prompt.truncated is True
    assert prompt.original_length == len(document.text or "")
    assert "third sentence" not in prompt.text


def test_unsupported_field_group_is_clear_error() -> None:
    document = RawDoc(source_url="https://example.test", text="ok")

    with pytest.raises(ExtractionConfigError, match="Unsupported field_group"):
        build_prompt(field_group="unknown_xyz", document=document)
