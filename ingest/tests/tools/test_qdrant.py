from __future__ import annotations

from crawler.tools.qdrant import InMemoryQdrantTool


def test_in_memory_qdrant_upsert_search_delete_and_sanitizes_raw_payload() -> None:
    index = InMemoryQdrantTool()

    assert index.upsert(
        "companies",
        [
            {
                "id": "neo-1",
                "text": "Acme is a digital bank and mobile banking app",
                "payload": {
                    "company_name": "Acme",
                    "raw_content": "secret page dump",
                    "nested": {"raw_html": "<html>secret</html>", "safe": "ok"},
                },
            }
        ],
    ) == ("neo-1",)

    results = index.search("companies", "Acme digital bank app", limit=1, min_score=0.1)

    assert len(results) == 1
    assert results[0].point_id == "neo-1"
    assert results[0].payload == {"company_name": "Acme", "nested": {"safe": "ok"}}
    assert "secret" not in str(results)
    assert index.delete("companies", ["neo-1", "missing"]) == ("neo-1",)
    assert index.search("companies", "Acme digital bank app") == ()


def test_qdrant_does_not_embed_raw_payload_text_when_text_missing() -> None:
    index = InMemoryQdrantTool()

    index.upsert(
        "companies",
        [
            {
                "id": "raw-1",
                "payload": {
                    "company_name": "Clean Bank",
                    "html_content": "unique boilerplate raw page words",
                },
            }
        ],
    )

    assert index.search("companies", "unique boilerplate raw page words", min_score=0.1) == ()
    assert index.search("companies", "Clean Bank", min_score=0.1)
