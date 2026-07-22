"""Reader core for attachment tools — FakeService + real in-memory zips."""
import io
import zipfile

from src.utils.attachment_reader import AttachmentToolContext, list_files, read_file, search_files

CAPS = {"read_max_chars": 100, "read_max_bytes": 1 << 20,
        "list_max_chars": 4000, "search_max_results": 5}


def _zip_bytes(entries: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return buf.getvalue()


class FakeService:
    def __init__(self, rows):
        self._rows = rows            # list of dicts with rung-1 item fields + original_bytes

    def get_context_items(self, conversation_id, include_text=True):
        return [{"filename": r["filename"], "kind": r["kind"],
                 "extracted_text": r["extracted_text"] if include_text else "",
                 "extraction_meta": r["extraction_meta"]}
                for r in self._rows]

    def get_for_tools(self, conversation_id, filename):
        for r in reversed(self._rows):
            if r["filename"] == filename:
                return dict(r)
        return None


def _ctx(rows):
    return AttachmentToolContext(conversation_id=7, service=FakeService(rows), caps=dict(CAPS))


def _bundle_row(name="proj.zip", entries=None):
    entries = entries or {"src/a.py": "VALUE = 1\n", "README.md": "# hi\n"}
    data = _zip_bytes(entries)
    index = [{"name": n, "size": len(c.encode() if isinstance(c, str) else c), "kind": "text"}
             for n, c in entries.items()]
    return {"filename": name, "kind": "bundle", "extracted_text": "",
            "extraction_meta": {"entries": index, "entry_count": len(index)},
            "original_bytes": data}


def _doc_row(name="notes.txt", text="alpha beta gamma"):
    return {"filename": name, "kind": "document", "extracted_text": text,
            "extraction_meta": {"extension": ".txt"}, "original_bytes": text.encode()}


def test_list_shows_attachments_and_entries():
    out = list_files(_ctx([_doc_row(), _bundle_row()]))
    assert "notes.txt" in out and "proj.zip" in out
    assert "src/a.py" in out and "README.md" in out


def test_read_document_with_offset_and_end_marker():
    out = read_file(_ctx([_doc_row(text="A" * 150)]), "notes.txt")
    assert out.count("A") == 100 and "offset=100" in out
    out2 = read_file(_ctx([_doc_row(text="A" * 150)]), "notes.txt", offset=100)
    assert out2.count("A") == 50 and "[end of file]" in out2


def test_read_bundle_entry_on_demand():
    out = read_file(_ctx([_bundle_row()]), "proj.zip", entry="src/a.py")
    assert "VALUE = 1" in out and "[end of file]" in out


def test_read_pdf_entry_routes_through_pdf_extractor(monkeypatch):
    import src.utils.attachment_reader as mod
    from src.utils.attachment_extraction import ExtractionResult
    row = _bundle_row(entries={"docs/m.pdf": "%PDF-fake"})
    row["extraction_meta"]["entries"][0]["kind"] = "pdf"
    monkeypatch.setattr(mod, "_extract_pdf",
                        lambda data, fn, poor: ExtractionResult(text="--- Page 1 ---\npdf text", kind="document"))
    out = read_file(_ctx([row]), "proj.zip", entry="docs/m.pdf")
    assert "pdf text" in out


def test_read_unknown_names_are_friendly():
    out = read_file(_ctx([_doc_row()]), "ghost.txt")
    assert "Not found" in out and "notes.txt" in out
    out2 = read_file(_ctx([_bundle_row()]), "proj.zip", entry="nope.py")
    assert "Not found" in out2 and "src/a.py" in out2


def test_read_binary_entry_friendly():
    row = _bundle_row(entries={"blob.bin": b"\x00\x01\x02\x03" * 8})
    out = read_file(_ctx([row]), "proj.zip", entry="blob.bin")
    assert "text" in out.lower()          # rung-1 friendly wording, no traceback


def test_offset_past_eof():
    out = read_file(_ctx([_doc_row(text="short")]), "notes.txt", offset=999)
    assert "past the end" in out and "5 chars" in out


def test_search_hits_documents_and_entries():
    rows = [_doc_row(text="needle in doc\nother"), _bundle_row(entries={"a.py": "no\nneedle here\n"})]
    out = search_files(_ctx(rows), "needle")
    assert "notes.txt:1" in out and "a.py:2" in out


def test_search_caps_results():
    text = "\n".join(f"needle {i}" for i in range(50))
    out = search_files(_ctx([_doc_row(text=text)]), "needle")
    assert out.count("needle") <= CAPS["search_max_results"] + 1   # +1 for the cap notice


def test_search_bad_regex_friendly():
    out = search_files(_ctx([_doc_row()]), "([", regex=True)
    assert "regex" in out.lower() and "error" not in out.lower() or "invalid" in out.lower()


def test_single_line_content_with_error_words_is_still_sliced():
    # One long line, no newline, containing an "error-ish" substring — must
    # still go through _slice's read_max_chars cap instead of being returned
    # raw because it happens to contain a word like "aren't".
    content = "data aren't here " * 20
    assert "\n" not in content and len(content) > 200
    row = _bundle_row(entries={"notes.log": content})
    out = read_file(_ctx([row]), "proj.zip", entry="notes.log")
    assert "[truncated at 100 chars — continue with offset=100]" in out
    piece = out.split("\n[truncated", 1)[0]
    assert len(piece) <= 100


def test_office_entry_refusal_not_sliced():
    row = _bundle_row(entries={"report.docx": "ignored placeholder bytes"})
    row["extraction_meta"]["entries"][0]["kind"] = "office"
    out = read_file(_ctx([row]), "proj.zip", entry="report.docx")
    assert "Office file" in out
    assert "[end of file]" not in out and "[truncated" not in out


def test_search_scan_budget_stops_early(monkeypatch):
    # Three ~100-char text entries, none matching the query, budget=50.
    # read_max_bytes gates BOTH the per-entry extraction read AND the search
    # scan budget (same cap key, by design — see fix wave 1), so each
    # extracted entry's text is itself capped at 50 chars: it takes two
    # entries (50 + 50 = 100 > 50) to trip the stop, and a single entry can
    # never exceed the budget on its own (its own contribution is bounded by
    # that same 50-char cap). That is exactly what distinguishes gated from
    # ungated extraction here: gated code stops after 2 calls (c.txt is never
    # reached); the pre-fix code built the full per-bundle `sources` list
    # before checking the budget at all, so it would have made 3 calls
    # regardless of where the budget fell.
    import src.utils.attachment_reader as mod
    entries = {"a.txt": "x" * 100, "b.txt": "y" * 100, "c.txt": "z" * 100}
    row = _bundle_row(entries=entries)
    ctx = AttachmentToolContext(conversation_id=7, service=FakeService([row]),
                                 caps={**CAPS, "read_max_bytes": 50})
    # search_files opens the bundle zip once and calls _extract_entry_from_zip
    # per entry (LAT-8), so count that inner call rather than _extract_entry.
    real = mod._extract_entry_from_zip
    calls = []
    monkeypatch.setattr(mod, "_extract_entry_from_zip",
                        lambda *a, **k: (calls.append(1), real(*a, **k))[1])
    out = search_files(ctx, "needle")
    assert "search stopped early" in out
    assert len(calls) == 2      # a.txt + b.txt extracted, c.txt never reached


def test_search_returns_hits_found_before_budget_stop():
    # The match on the FIRST entry must be returned even though it takes a
    # SECOND entry to actually trip the budget (per the same read_max_bytes
    # coupling explained above, one ~100-char entry alone can reach the
    # 50-char budget but never exceed it) — proving that matching an entry's
    # text happens in full before the running total is checked, not that the
    # stop suppresses hits already found.
    entries = {"a.txt": "needle here\n" + "x" * 88, "b.txt": "y" * 100}
    row = _bundle_row(entries=entries)
    ctx = AttachmentToolContext(conversation_id=7, service=FakeService([row]),
                                 caps={**CAPS, "read_max_bytes": 50})
    out = search_files(ctx, "needle")
    assert "a.txt:1" in out and "needle here" in out
    assert "search stopped early" in out


def test_read_corrupted_entry_stream_is_friendly():
    # Flip bytes inside the DEFLATE stream itself (not the central directory)
    # so zipfile raises a bare zlib.error/ValueError while streaming the
    # entry — distinct from zipfile.BadZipFile/RuntimeError, which is why a
    # narrow except tuple around the read step lets corruption crash the tool
    # call instead of degrading to a friendly string.
    import struct

    entries = {"a.txt": "hello world, this is a test payload. " * 500}
    data = bytearray(_zip_bytes(entries))
    namelen, extralen = struct.unpack("<HH", data[26:30])
    comp_size = struct.unpack("<I", data[18:22])[0]
    data_start = 30 + namelen + extralen
    for i in range(data_start, data_start + 6):
        data[i] ^= 0xFF
    index = [{"name": "a.txt", "size": len(entries["a.txt"]), "kind": "text"}]
    row = {"filename": "proj.zip", "kind": "bundle", "extracted_text": "",
           "extraction_meta": {"entries": index, "entry_count": 1},
           "original_bytes": bytes(data)}
    out = read_file(_ctx([row]), "proj.zip", entry="a.txt")
    assert "unreadable" in out.lower() or "corrupt" in out.lower()


# --- LAT-3: list prefix/glob filter -----------------------------------------

def test_list_prefix_filter_forgiving_and_header():
    # "src/" must match "T0-master/src/a.py" even though the entry does not
    # start with it (forgiving of the missing zip-root segment via "/src/").
    row = _bundle_row(name="T0.zip", entries={
        "T0-master/src/a.py": "x", "T0-master/README.md": "y", "top.txt": "z"})
    out = list_files(_ctx([row]), prefix="src/")
    assert "T0-master/src/a.py" in out
    assert "README.md" not in out and "top.txt" not in out
    assert 'filtered: prefix="src/"' in out
    assert "1 of 4 entries" in out   # 1 match of (1 bundle name + 3 entries)


def test_list_prefix_startswith_and_leading_slash():
    row = _bundle_row(name="p.zip", entries={"src/a.py": "x", "docs/b.md": "y"})
    out = list_files(_ctx([row]), prefix="/src/")     # leading slash normalized away
    assert "src/a.py" in out and "docs/b.md" not in out


def test_list_glob_filter():
    row = _bundle_row(name="p.zip", entries={"src/a.py": "x", "src/b.md": "y", "c.py": "z"})
    out = list_files(_ctx([row]), glob="*.py")
    assert "src/a.py" in out and "c.py" in out
    assert "src/b.md" not in out
    assert 'glob="*.py"' in out


def test_list_prefix_no_match_message():
    row = _bundle_row(name="p.zip", entries={"src/a.py": "x"})
    out = list_files(_ctx([row]), prefix="nope/")
    assert out == ('No entries match prefix "nope/". Call list_attachment_files '
                   "with no filter to see every path.")


def test_list_index_truncation_note():
    # FE-08: entry_count exceeds the number of indexed (addressable) entries.
    row = _bundle_row(name="big.zip", entries={"a.py": "x", "b.py": "y"})
    row["extraction_meta"]["entry_count"] = 5000
    out = list_files(_ctx([row]))
    assert "[index truncated: only the first 2 of 5000 entries are addressable]" in out


# --- LAT-5: empty-file wording ----------------------------------------------

def test_read_empty_document_reports_empty():
    out = read_file(_ctx([_doc_row(text="")]), "notes.txt")
    assert out == "(file is empty — 0 chars)"


def test_read_empty_bundle_entry_reports_empty():
    row = _bundle_row(entries={"empty.txt": ""})
    out = read_file(_ctx([row]), "proj.zip", entry="empty.txt")
    assert out == "(file is empty — 0 chars)"


# --- CF3: raw read mode -----------------------------------------------------

def test_raw_mode_recovers_script_from_html_bundle_entry():
    # CF3: raw mode skips HTML extraction and returns the stored bytes exactly,
    # so the literal <script> tag survives. How default text-mode extraction
    # reshapes HTML is a sibling module's concern — we only require that raw is
    # verbatim and that text-mode is NOT the same verbatim bytes.
    html = "<html><body><script>SECRET=42</script><p>hi</p></body></html>"
    row = _bundle_row(entries={"page.html": html})
    out_raw = read_file(_ctx([row]), "proj.zip", entry="page.html", mode="raw")
    assert "<script>SECRET=42</script>" in out_raw and "<html>" in out_raw
    out_text = read_file(_ctx([row]), "proj.zip", entry="page.html")
    assert out_text != out_raw


def test_raw_mode_reads_original_bytes_for_document():
    row = _doc_row(name="a.txt", text="stale extracted text")
    row["original_bytes"] = b"the real original bytes"
    out = read_file(_ctx([row]), "a.txt", mode="raw")
    assert "the real original bytes" in out and "stale extracted" not in out


def test_raw_mode_non_decodable_bytes_friendly():
    row = _doc_row(name="b.dat", text="placeholder")
    row["original_bytes"] = b"\x00\x01\x02\x03\x04binary"
    out = read_file(_ctx([row]), "b.dat", mode="raw")
    assert "not decodable text" in out


def test_invalid_mode_falls_back_to_text():
    out = read_file(_ctx([_doc_row(text="hello world")]), "notes.txt", mode="bogus")
    assert "hello world" in out


# --- LAT-2: search matches file names + new no-match message ----------------

def test_search_matches_file_names():
    # A path-only query (no content hit) must still surface via the name path.
    row = _bundle_row(name="proj.zip", entries={"T0-master/config.py": "unrelated body\n"})
    out = search_files(_ctx([row]), "T0-master")
    assert "proj.zip:T0-master/config.py (file name matches)" in out


def test_search_no_content_match_message():
    out = search_files(_ctx([_doc_row(text="alpha beta")]), "zzz-absent")
    assert out.startswith('No content matches for "zzz-absent".')
    assert "file NAMES" in out and "list_attachment_files(prefix=" in out


# --- LAT-8: one ZipFile per bundle per search -------------------------------

def test_search_opens_each_bundle_zip_once(monkeypatch):
    import src.utils.attachment_reader as mod
    row = _bundle_row(entries={"a.txt": "aa\n", "b.txt": "bb\n", "c.txt": "cc\n"})
    ctx = _ctx([row])
    real_zipfile = mod.zipfile.ZipFile
    count = {"n": 0}

    def counting(*a, **k):
        count["n"] += 1
        return real_zipfile(*a, **k)

    monkeypatch.setattr(mod.zipfile, "ZipFile", counting)
    search_files(ctx, "no-such-token")
    assert count["n"] == 1   # one bundle: its zip opened once for all 3 entries


# --- C2: honest truncation for byte-capped bundle entries --------------------

def test_read_bundle_entry_over_byte_cap_marks_truncation():
    # An entry larger than read_max_bytes is read byte-capped, so paginating to
    # its end must NOT say "[end of file]" (a lie: content past the cap exists).
    row = _bundle_row(entries={"big.log": "L" * 400})
    ctx = AttachmentToolContext(conversation_id=7, service=FakeService([row]),
                                caps={**CAPS, "read_max_bytes": 50, "read_max_chars": 100})
    out = read_file(ctx, "proj.zip", entry="big.log")
    assert "[end of file]" not in out
    assert "read cap" in out.lower() and "can't be paged" in out.lower()


def test_search_bundle_entry_over_byte_cap_notes_incomplete_scan():
    # A match sitting PAST the byte cap is missed; search must flag the partial
    # scan rather than reporting a clean absence.
    body = "A" * 60 + "\nNEEDLE_TOKEN\n"     # NEEDLE_TOKEN is past the 50-byte cap
    row = _bundle_row(entries={"big.log": body})
    ctx = AttachmentToolContext(conversation_id=7, service=FakeService([row]),
                                caps={**CAPS, "read_max_bytes": 50})
    out = search_files(ctx, "NEEDLE_TOKEN")
    assert "NEEDLE_TOKEN" not in out          # never scanned, so not a real hit
    assert "per-read limit" in out and "not conclusive" in out


# --- C3: duplicate filenames are disambiguated, not silently collapsed -------

def test_list_annotates_duplicate_filenames():
    older = _doc_row(name="data.csv", text="old")
    newer = _doc_row(name="data.csv", text="new")
    out = list_files(_ctx([older, newer]))
    assert "duplicate name" in out
    assert out.count("data.csv") >= 2


def test_read_duplicate_filename_notes_shadowed():
    older = _doc_row(name="data.csv", text="OLD rows")
    newer = _doc_row(name="data.csv", text="NEW rows")
    out = read_file(_ctx([older, newer]), "data.csv")
    assert "NEW rows" in out              # newest resolves
    assert "OLD rows" not in out          # older is shadowed / unreadable by name
    assert 'named "data.csv"' in out and "most recent" in out


class _SpyService:
    """Records the include_text flag of each get_context_items call."""
    def __init__(self, items):
        self._items = items
        self.calls = []

    def get_context_items(self, cid, include_text=True):
        self.calls.append(include_text)
        return self._items

    def get_for_tools(self, cid, filename):
        for it in reversed(self._items):
            if it["filename"] == filename:
                return {**it, "original_bytes": (it.get("extracted_text") or "").encode()}
        return None


def test_list_does_not_stream_extracted_text():
    # list_files renders only filename/kind/size/meta, so it must fetch metadata
    # only — not stream every attachment's full extracted_text out of Postgres.
    svc = _SpyService([{"filename": "a.txt", "kind": "document",
                        "extracted_text": "x" * 10_000, "extraction_meta": {}}])
    ctx = AttachmentToolContext(conversation_id=7, service=svc, caps=dict(CAPS))
    list_files(ctx)
    assert svc.calls == [False]        # metadata-only fetch


def test_search_still_fetches_extracted_text():
    # search_files scans non-bundle content from the batch fetch, so it must
    # keep pulling the text.
    svc = _SpyService([{"filename": "a.txt", "kind": "document",
                        "extracted_text": "the needle is here", "extraction_meta": {}}])
    ctx = AttachmentToolContext(conversation_id=7, service=svc, caps=dict(CAPS))
    out = search_files(ctx, "needle")
    assert True in svc.calls           # search needs the text
    assert "needle" in out


def test_read_large_pdf_entry_reads_whole_file_past_paging_cap(monkeypatch):
    # A PDF entry larger than read_max_bytes must still be extractable: a PDF's
    # xref/trailer lives at the END of the file, so a byte-capped prefix can't
    # be parsed. The paging byte cap must not gate whole-document formats.
    import src.utils.attachment_reader as mod
    from src.utils.attachment_extraction import ExtractionResult
    big_pdf = "%PDF-1.7\n" + ("padding " * 40)     # ~330 bytes, well over the tiny cap
    row = _bundle_row(entries={"docs/big.pdf": big_pdf})
    row["extraction_meta"]["entries"][0]["kind"] = "pdf"
    seen = {}

    def fake_pdf(data, fn, poor):
        seen["len"] = len(data)                    # how many bytes reached the extractor
        return ExtractionResult(text="PDF BODY TEXT", kind="document")

    monkeypatch.setattr(mod, "_extract_pdf", fake_pdf)
    ctx = AttachmentToolContext(conversation_id=7, service=FakeService([row]),
                                caps={**CAPS, "read_max_bytes": 10})   # tiny paging cap
    out = read_file(ctx, "proj.zip", entry="docs/big.pdf")
    assert "PDF BODY TEXT" in out
    assert seen["len"] == len(big_pdf.encode()), "the whole PDF must reach the extractor"


def test_search_name_matches_do_not_starve_content_in_later_entries():
    # Many entries whose NAMES match the query, plus a LATER entry whose CONTENT
    # (not name) holds the answer. Filename matches must not fill the result cap
    # and stop the scan before the content match in the later entry is found.
    entries = {f"config/app{i}.py": "unrelated body\n" for i in range(6)}   # 6 > cap 5
    entries["zzz/answer.py"] = "the config KNOB lives here\n"               # content match
    row = _bundle_row(name="proj.zip", entries=entries)
    out = search_files(_ctx([row]), "config")
    assert "the config KNOB lives here" in out


def test_search_charges_bytes_not_chars_against_scan_budget():
    # The scan budget is a BYTE budget (read_max_bytes); multi-byte text must be
    # charged its byte length, not its smaller character count, or the CPU guard
    # runs ~3x longer than configured. 100 Thai chars = 300 bytes: charging bytes
    # trips a 250-byte budget after two entries; charging chars (100) would not.
    thai = "ก" * 100                                # 100 chars, 300 UTF-8 bytes
    row = _bundle_row(entries={"a.txt": thai, "b.txt": "y" * 100})
    ctx = AttachmentToolContext(conversation_id=7, service=FakeService([row]),
                                caps={**CAPS, "read_max_bytes": 250, "search_max_results": 20})
    out = search_files(ctx, "no-match-token")
    assert "search stopped early" in out


def test_search_duplicate_bundle_scans_newest_once_and_notes(monkeypatch):
    import src.utils.attachment_reader as mod
    older = _bundle_row(name="proj.zip", entries={"a.txt": "old_marker here\n"})
    newer = _bundle_row(name="proj.zip", entries={"a.txt": "new_marker here\n"})
    ctx = _ctx([older, newer])            # get_context_items order = created_at ASC
    real_zipfile = mod.zipfile.ZipFile
    count = {"n": 0}

    def counting(*a, **k):
        count["n"] += 1
        return real_zipfile(*a, **k)

    monkeypatch.setattr(mod.zipfile, "ZipFile", counting)
    out = search_files(ctx, "marker")
    assert count["n"] == 1                # newest opened once, not scanned twice
    assert "new_marker" in out            # the reachable (newest) content is searched
    assert "old_marker" not in out        # the older bundle is unreachable by name
    assert "older attachment shares a name" in out
