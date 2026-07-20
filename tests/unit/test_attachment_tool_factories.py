from src.archi.pipelines.agents.tools.attachment_tools import (
    create_attachment_list_tool,
    create_attachment_read_tool,
    create_attachment_search_tool,
)
from src.utils.attachment_reader import AttachmentToolContext


class _Svc:
    def get_context_items(self, cid, include_text=True):
        return [{"filename": "notes.txt", "kind": "document",
                 "extracted_text": "hello tools" if include_text else "",
                 "extraction_meta": {}}]

    def get_for_tools(self, cid, filename):
        if filename != "notes.txt":
            return None
        return {"filename": "notes.txt", "kind": "document", "extracted_text": "hello tools",
                "extraction_meta": {}, "original_bytes": b"hello tools"}


CTX = AttachmentToolContext(conversation_id=7, service=_Svc(),
                            caps={"read_max_chars": 100, "read_max_bytes": 1024,
                                  "list_max_chars": 1000, "search_max_results": 5})


def test_tool_names_and_invocation():
    lst, red, srch = (create_attachment_list_tool(CTX),
                      create_attachment_read_tool(CTX),
                      create_attachment_search_tool(CTX))
    assert lst.name == "list_attachment_files"
    assert red.name == "read_attachment_file"
    assert srch.name == "search_attachment_files"
    assert "notes.txt" in lst.invoke({})
    assert "hello tools" in red.invoke({"filename": "notes.txt"})
    assert "notes.txt:1" in srch.invoke({"query": "hello"})


def test_read_tool_never_raises_on_bad_input():
    red = create_attachment_read_tool(CTX)
    out = red.invoke({"filename": "ghost.txt"})
    assert "Not found" in out


def test_descriptions_mention_manifest_workflow():
    lst = create_attachment_list_tool(CTX)
    red = create_attachment_read_tool(CTX)
    srch = create_attachment_search_tool(CTX)
    assert "conversation" in lst.description
    assert "offset" in red.description
    assert "MANIFEST ONLY" in lst.description
    assert "entry" in red.description
    assert "regex" in srch.description


class _RaisingSvc:
    def get_context_items(self, cid, include_text=True):
        raise RuntimeError("db down")

    def get_for_tools(self, cid, filename):
        raise RuntimeError("db down")


def test_wrappers_catch_reader_exceptions():
    # The wrappers are the system's "never raises toward the agent" guarantee:
    # a reader/DB failure must come back as the generic fallback string.
    ctx = AttachmentToolContext(conversation_id=7, service=_RaisingSvc(), caps={})
    assert create_attachment_list_tool(ctx).invoke({}) == "Listing attachments failed unexpectedly."
    assert create_attachment_read_tool(ctx).invoke({"filename": "x"}) == "Reading the attachment failed unexpectedly."
    assert create_attachment_search_tool(ctx).invoke({"query": "q"}) == "Searching attachments failed unexpectedly."


def test_new_params_surface_on_the_tools():
    # list exposes prefix/glob; read exposes mode — invoking with them must not
    # raise and must reach the reader (prefix that matches nothing => friendly
    # no-match; raw mode => original bytes).
    lst = create_attachment_list_tool(CTX)
    assert "No entries match" in lst.invoke({"prefix": "zzz/"})
    red = create_attachment_read_tool(CTX)
    assert "hello tools" in red.invoke({"filename": "notes.txt", "mode": "raw"})


def test_descriptions_mention_new_capabilities():
    lst = create_attachment_list_tool(CTX)
    red = create_attachment_read_tool(CTX)
    srch = create_attachment_search_tool(CTX)
    assert "prefix" in lst.description and "0 B" in lst.description
    assert "raw" in red.description
    assert "file names" in srch.description.lower()


def test_store_tool_input_captures_resolved_args():
    # CONTRACT: base_react passes store_tool_input=<callback> as a keyword arg;
    # each tool records its resolved args before doing work.
    captured = []
    sink = lambda name, args: captured.append((name, args))  # noqa: E731
    create_attachment_list_tool(CTX, store_tool_input=sink).invoke({"prefix": "src/", "glob": "*.py"})
    create_attachment_read_tool(CTX, store_tool_input=sink).invoke(
        {"filename": "notes.txt", "entry": "", "offset": 0, "mode": "raw"})
    create_attachment_search_tool(CTX, store_tool_input=sink).invoke({"query": "hello", "regex": True})
    assert ("list_attachment_files", {"prefix": "src/", "glob": "*.py"}) in captured
    assert ("read_attachment_file",
            {"filename": "notes.txt", "entry": "", "offset": 0, "mode": "raw"}) in captured
    assert ("search_attachment_files", {"query": "hello", "regex": True}) in captured


def test_store_tool_input_failure_does_not_break_the_tool():
    # A raising callback must be swallowed — the tool still returns its result.
    def boom(name, args):
        raise RuntimeError("sink down")

    out = create_attachment_read_tool(CTX, store_tool_input=boom).invoke({"filename": "notes.txt"})
    assert "hello tools" in out
