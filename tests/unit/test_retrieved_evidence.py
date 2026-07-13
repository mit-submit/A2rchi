from langchain_core.documents import Document
from langchain_core.messages import ToolMessage

from src.archi.utils.output_dataclass import PipelineOutput
from src.interfaces.chat_app.event_formatter import PipelineEventFormatter
from src.archi.pipelines.agents.utils.retrieved_evidence import (
    build_retrieved_evidence_payload,
    documents_with_scores,
    evidence_item_from_document,
)
from src.archi.pipelines.agents.utils.run_memory import RunMemory


def test_evidence_item_for_standalone_image():
    doc = Document(
        page_content="A detector photo with cables.",
        metadata={
            "resource_hash": "img-hash",
            "display_name": "detector.png",
            "source_type": "files",
            "chunk_source": "vision_caption",
        },
    )

    item = evidence_item_from_document(doc, score=0.88, rank=1)

    assert item["kind"] == "image"
    assert item["source"]["resource_hash"] == "img-hash"
    assert item["preview"]["type"] == "image"
    assert item["score"] == 0.88
    assert "image" not in item
    assert "bytes" not in item


def test_evidence_item_for_pdf_caption_page_metadata():
    doc = Document(
        page_content="Caption for page three.",
        metadata={
            "resource_hash": "pdf-hash",
            "display_name": "paper.pdf",
            "chunk_source": "vision_caption",
            "page_number": "3",
        },
    )

    item = evidence_item_from_document(doc, rank=1)

    assert item["kind"] == "pdf_page_caption"
    assert item["page"]["page_number"] == 3
    assert item["page"]["page_index"] == 2
    assert item["preview"]["type"] == "pdf_page"


def test_evidence_item_for_pdf_text_page_fallback_from_zero_based_page():
    doc = Document(
        page_content="Extracted page text.",
        metadata={
            "resource_hash": "pdf-hash",
            "display_name": "paper.pdf",
            "page": 0,
        },
    )

    item = evidence_item_from_document(doc, rank=1)

    assert item["kind"] == "pdf_page_text"
    assert item["page"]["page_number"] == 1
    assert item["page"]["page_index"] == 0


def test_evidence_item_for_pdf_text_without_page_falls_back_to_text():
    doc = Document(
        page_content="Extracted text with no page metadata.",
        metadata={"resource_hash": "pdf-hash", "display_name": "paper.pdf"},
    )

    item = evidence_item_from_document(doc, rank=1)

    assert item["kind"] == "text"
    assert item["page"] is None
    assert item["preview"]["type"] == "text"


def test_evidence_item_preserves_zero_based_text_chunk_index():
    doc = Document(
        page_content="First code chunk.",
        metadata={"resource_hash": "code", "display_name": "module.py", "chunk_index": 0},
    )

    item = evidence_item_from_document(doc, rank=1)

    assert item["kind"] == "text"
    assert item["retrieved_unit"]["chunk_index"] == 0
    assert item["retrieved_unit"]["id"] == "chunk:0"


def test_payload_groups_and_deduplicates_by_resource_and_unit():
    docs = [
        Document(
            page_content="First chunk",
            metadata={"resource_hash": "a", "display_name": "a.txt", "chunk_id": "c1"},
        ),
        Document(
            page_content="Duplicate chunk",
            metadata={"resource_hash": "a", "display_name": "a.txt", "chunk_id": "c1"},
        ),
        Document(
            page_content="Second chunk",
            metadata={"resource_hash": "a", "display_name": "a.txt", "chunk_id": "c2"},
        ),
    ]

    payload = build_retrieved_evidence_payload(docs)

    assert len(payload["groups"]) == 1
    assert len(payload["items"]) == 2
    assert len(payload["groups"][0]["items"]) == 2


def test_payload_collapses_multiple_image_chunks_to_single_visual_item():
    docs = [
        Document(
            page_content="First image caption",
            metadata={
                "resource_hash": "img",
                "display_name": "diagram.png",
                "chunk_id": "caption-1",
            },
        ),
        Document(
            page_content="Second image caption",
            metadata={
                "resource_hash": "img",
                "display_name": "diagram.png",
                "chunk_id": "caption-2",
            },
        ),
    ]

    payload = build_retrieved_evidence_payload(docs)

    assert len(payload["items"]) == 1
    assert payload["items"][0]["kind"] == "image"
    assert payload["groups"][0]["items"][0]["retrieved_unit"]["id"] == "image"


def test_payload_collapses_multiple_pdf_chunks_on_same_page_to_single_page_item():
    docs = [
        Document(
            page_content="Page text",
            metadata={
                "resource_hash": "pdf",
                "display_name": "manual.pdf",
                "chunk_id": "text-1",
                "page_number": 2,
            },
        ),
        Document(
            page_content="Page caption",
            metadata={
                "resource_hash": "pdf",
                "display_name": "manual.pdf",
                "chunk_id": "caption-1",
                "chunk_source": "vision_caption",
                "page_number": 2,
            },
        ),
    ]

    payload = build_retrieved_evidence_payload(docs)

    assert len(payload["items"]) == 1
    assert payload["items"][0]["preview"]["type"] == "pdf_page"
    assert payload["items"][0]["retrieved_unit"]["id"] == "page:2"


def test_documents_with_scores_keeps_score_out_of_llm_formatting_path():
    original = Document(page_content="Hello", metadata={"resource_hash": "h"})

    copied = documents_with_scores([(original, 0.42)])

    assert copied[0] is not original
    assert copied[0].metadata["retriever_score"] == 0.42
    assert "retriever_score" not in original.metadata


def test_run_memory_evidence_contains_metadata_and_excerpts_only():
    memory = RunMemory()
    memory.record_documents(
        "search",
        [
            Document(
                page_content="A" * 1400,
                metadata={
                    "resource_hash": "unsupported",
                    "display_name": "archive.bin",
                    "image_bytes": b"not copied",
                },
            )
        ],
    )

    payload = memory.retrieved_evidence()
    item = payload["items"][0]

    assert item["kind"] == "unsupported"
    assert item["preview"]["preview_unavailable"] is True
    assert len(item["excerpt"]) <= 1203
    assert "image_bytes" not in str(payload)


def test_formatter_emits_evidence_event_after_tool_output_without_binary_payload():
    payload = build_retrieved_evidence_payload(
        [
            Document(
                page_content="Caption text",
                metadata={
                    "resource_hash": "image-hash",
                    "display_name": "image.png",
                    "image_bytes": b"not copied",
                },
            )
        ]
    )
    output = PipelineOutput(
        answer="",
        messages=[ToolMessage(content="tool output", tool_call_id="call-1")],
        metadata={
            "event_type": "tool_output",
            "retrieved_evidence": payload,
            "tool_inputs_by_id": {
                "call-1": {"tool_name": "search_vectorstore_hybrid", "tool_input": {"query": "q"}}
            },
        },
        final=False,
    )
    formatter = PipelineEventFormatter(message_content_fn=lambda msg: msg.content)

    events = list(formatter.process(output))

    assert [event["type"] for event in events] == ["tool_start", "tool_output", "retrieved_evidence"]
    assert events[-1]["evidence"]["items"][0]["source"]["resource_hash"] == "image-hash"
    assert "not copied" not in str(events[-1])
