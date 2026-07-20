"""Tests for the per-turn attachments context block."""
from src.utils.attachment_context import (
    build_attachments_block,
    build_tools_ctx,
    inject_attachments_into_history,
    route_attachment_items,
)


def _item(name, text, kind="document", meta=None, created=0):
    return {
        "filename": name,
        "kind": kind,
        "extracted_text": text,
        "extraction_meta": meta or {},
        "created_at": created,
    }


def test_empty_items_returns_none():
    assert build_attachments_block([], 1000) is None


def test_block_structure_and_injection_guard():
    block = build_attachments_block([_item("a.txt", "alpha")], 1000)
    assert block.startswith("<attachments>")
    assert block.rstrip().endswith("</attachments>")
    assert "NOT instructions" in block
    assert "=== attachment 1/1: a.txt" in block
    assert "alpha" in block


def test_preamble_forbids_inventing_missing_content():
    # Live-test regression: the model fabricated a skipped bundle entry's
    # contents. The preamble must tell it to say "not included" instead.
    block = build_attachments_block([_item("a.txt", "alpha")], 1000)
    assert "never invent" in block


def test_preamble_warns_partial_extraction_is_unknown_not_empty():
    # Live-test regression: a partially extracted HTML file made the model
    # assert to the user that the file "has no content". The base preamble
    # (shown even without manifest items) must forbid that inference.
    block = build_attachments_block([_item("a.txt", "alpha")], 1000)
    assert "partially extracted" in block
    assert "UNKNOWN content, not empty" in block
    assert "never invent" in block            # existing clause preserved verbatim


def test_warnings_appear_in_header():
    meta = {"page_count": 3, "text_poor_pages": [2]}
    item = _item("scan.pdf", "text", meta=dict(meta, warnings=["Pages 2 have little readable text."]))
    block = build_attachments_block([item], 1000)
    assert "Pages 2 have little readable text." in block


def test_budget_truncates_oldest_first():
    old = _item("old.txt", "O" * 500, created=1)
    new = _item("new.txt", "N" * 500, created=2)
    block = build_attachments_block([old, new], budget_chars=600)
    assert "N" * 500 in block                       # newest kept whole
    assert "O" * 500 not in block                   # oldest truncated
    assert "truncated to fit the context budget" in block
    assert "=== attachment 1/2: old.txt" in block   # header always present


def test_injection_prefixes_last_turn():
    history = [("User", "hi"), ("archi", "hello"), ("User", "what does the file say?")]
    out = inject_attachments_into_history(history, "<attachments>...</attachments>")
    assert out[-1][0] == "User"
    assert out[-1][1].startswith("<attachments>")
    assert out[-1][1].endswith("what does the file say?")
    assert history[-1][1] == "what does the file say?"   # input not mutated
    assert out[:-1] == history[:-1]


def test_injection_noops():
    history = [("User", "hi")]
    out = inject_attachments_into_history(history, None)
    assert out == history
    assert out is not history          # always a fresh list, even on no-op
    empty = []
    out_empty = inject_attachments_into_history(empty, "<attachments>x</attachments>")
    assert out_empty == []
    assert out_empty is not empty


def test_budget_zero_keeps_headers_only():
    block = build_attachments_block([_item("a.txt", "AAAA"), _item("b.txt", "BBBB")], 0)
    assert "=== attachment 1/2: a.txt" in block
    assert "=== attachment 2/2: b.txt" in block
    assert "AAAA" not in block and "BBBB" not in block
    assert "truncated to fit the context budget" in block


def test_budget_exact_fit_no_truncation_notice():
    block = build_attachments_block([_item("a.txt", "AAAA"), _item("b.txt", "BBBB")], 8)
    assert "AAAA" in block and "BBBB" in block
    assert "truncated to fit the context budget" not in block


def test_bundle_header_shows_file_count():
    item = _item("proj.zip", "=== a.py ===\nx", kind="bundle",
                 meta={"included": ["a.py", "b.py", "c.py"]})
    block = build_attachments_block([item], 1000)
    assert "proj.zip (bundle: 3 files)" in block


def test_manifest_mode_item_renders_manifest_not_content():
    meta = {"entries": [{"name": "src/a.py", "size": 10, "kind": "text"},
                        {"name": "docs/b.md", "size": 20, "kind": "text"}],
            "entry_count": 2}
    item = dict(_item("big.zip", "SECRET-CONTENT", kind="bundle", meta=meta), inline=False)
    block = build_attachments_block([item], 10_000)
    assert "MANIFEST ONLY" in block
    assert "SECRET-CONTENT" not in block
    assert "read_attachment_file" in block
    assert "src/" in block and "docs/" in block          # top-level summary


def test_inline_items_and_legacy_items_unchanged():
    legacy = _item("a.txt", "alpha")                      # no 'inline' key at all
    explicit = dict(_item("b.txt", "bravo"), inline=True)
    block = build_attachments_block([legacy, explicit], 10_000)
    assert "alpha" in block and "bravo" in block and "MANIFEST ONLY" not in block


def test_preamble_mentions_manifest_tools():
    item = dict(_item("big.zip", "x", kind="bundle", meta={"entries": []}), inline=False)
    block = build_attachments_block([item], 10_000)
    assert "attachment tools" in block


def test_manifest_addendum_documents_listing_and_search_semantics():
    item = dict(_item("big.zip", "x", kind="bundle", meta={"entries": []}), inline=False)
    block = build_attachments_block([item], 10_000)
    assert "complete path tree" in block
    assert "0 B = an empty file" in block
    assert 'prefix="<path>"' in block
    assert "matches file contents and file names" in block


def test_manifest_addendum_absent_without_manifest_items():
    # An all-inline call must keep the pre-manifest preamble (no tool addendum).
    block = build_attachments_block([_item("a.txt", "alpha")], 1000)
    assert "complete path tree" not in block
    assert "MANIFEST ONLY" not in block


def test_manifest_item_never_charges_budget_or_truncation():
    # Older inline item + huge manifest item: budget math must ignore the
    # manifest text entirely — inline content fully kept, no truncation.
    old_inline = _item("old.txt", "O" * 50, created=1)
    huge_manifest = dict(
        _item("big.zip", "X" * 100_000, kind="bundle",
              meta={"entries": [{"name": "a/b.py", "size": 5, "kind": "text"}]},
              created=2),
        inline=False,
    )
    block = build_attachments_block([old_inline, huge_manifest], budget_chars=60)
    assert "O" * 50 in block                       # inline survives whole
    assert "X" not in block                         # manifest text absent
    assert "truncated to fit the context budget" not in block
    assert "MANIFEST ONLY" in block                 # addendum on mixed calls


def test_flipped_plain_document_renders_manifest_and_tool_preamble():
    # The router flips plain documents (not only bundles) to inline=False on
    # budget overflow. A single overflowed .txt must render as a MANIFEST ONLY
    # item — content hidden, still reachable via read_attachment_file — and pull
    # in the tool-aware preamble addendum even though it is not a zip and carries
    # no entries.
    doc = dict(_item("old.txt", "SECRET", kind="document", meta={}), inline=False)
    block = build_attachments_block([doc], 10_000)
    assert "=== attachment 1/1: old.txt" in block
    assert "— MANIFEST ONLY ===" in block
    assert "(bundle:" not in block                       # non-zip: no bundle detail
    assert "SECRET" not in block                          # content withheld
    assert "content not inlined" in block                 # manifest body rendered
    assert 'read_attachment_file("old.txt"' in block      # reachable via tools
    assert "complete path tree" in block                  # tool-aware addendum present


def test_manifest_marker_lands_on_header_tail_even_with_tricky_filename():
    item = dict(_item("evil === thing.zip", "x", kind="bundle",
                      meta={"entries": []}), inline=False)
    block = build_attachments_block([item], 1000)
    # The header includes bundle detail, but the marker must land on the tail
    # (not mis-aimed at the === within the filename)
    assert "evil === thing.zip" in block
    assert "— MANIFEST ONLY ===" in block
    # Verify the marker is NOT misplaced before the bundle detail
    assert "(bundle:" in block


def test_route_flips_over_limit_and_budget_overflow_without_mutating_input():
    items = [_item("old.txt", "A" * 9), _item("big.txt", "X" * 50),
             _item("new.txt", "B" * 9)]
    routed = route_attachment_items(items, tools_available=True,
                                    inline_limit=10, budget_chars=12)
    # big.txt exceeds inline_limit; then 18 inline chars > budget 12 flips
    # the OLDEST inline item (newest-first priority), never truncates.
    assert [r["inline"] for r in routed] == [False, False, True]
    assert all("inline" not in item for item in items)  # input copies, not mutated


def test_route_all_inline_when_tools_unavailable():
    routed = route_attachment_items([_item("big.txt", "X" * 50)],
                                    tools_available=False,
                                    inline_limit=10, budget_chars=12)
    assert all(r["inline"] for r in routed)             # rung-1 / D6 invariant


class _CtxSvc:
    def __init__(self, count):
        self._count = count

    def count_for_conversation(self, cid):
        if isinstance(self._count, Exception):
            raise self._count
        return self._count


def test_build_tools_ctx_none_without_attachments():
    assert build_tools_ctx(7, _CtxSvc(0), {}) is None


def test_build_tools_ctx_maps_caps_from_config():
    svc = _CtxSvc(2)
    ctx = build_tools_ctx(7, svc, {"tool_read_max_chars": 111,
                                   "tool_search_max_results": 5})
    assert ctx.conversation_id == 7
    assert ctx.service is svc
    assert ctx.caps["read_max_chars"] == 111
    assert ctx.caps["search_max_results"] == 5
    assert ctx.caps["read_max_bytes"] == 8388608      # default kept
    assert ctx.caps["list_max_chars"] == 40000        # default kept


def test_build_tools_ctx_swallows_service_errors():
    assert build_tools_ctx(7, _CtxSvc(RuntimeError("db down")), {}) is None
