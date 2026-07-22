"""Bundle (zip) extraction tests — real in-memory zips, stdlib only."""
import io
import struct
import zipfile

import pytest

from src.utils.attachment_extraction import AttachmentRejected
from src.utils.attachment_bundle import extract_bundle

CFG = {
    "zip_max_decompressed_mb": 1,
    "zip_max_entries": 5,
    "text_poor_page_chars": 50,
    "text_budget_chars": 400000,
}


def _zip_bytes(entries: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return buf.getvalue()


def test_bundle_stitches_supported_entries():
    data = _zip_bytes({"src/main.py": "print('a')", "README.md": "# readme"})
    res = extract_bundle("proj.zip", data, CFG)
    assert res.kind == "bundle"
    assert "=== src/main.py ===" in res.text and "print('a')" in res.text
    assert "=== README.md ===" in res.text
    assert res.meta["included"] == ["README.md", "src/main.py"]  # sorted
    assert res.meta["skipped"] == []


def test_bundle_skips_unsupported_with_reasons():
    data = _zip_bytes({
        "a.py": "x = 1",
        "logo.png": "fakebytes",
        "doc.docx": "fakebytes",
        "inner.zip": "fakebytes",
    })
    res = extract_bundle("proj.zip", data, CFG)
    assert res.meta["included"] == ["a.py"]
    reasons = {s["name"]: s["reason"] for s in res.meta["skipped"]}
    assert "image" in reasons["logo.png"]
    assert "nested archive" in reasons["inner.zip"]
    assert any("skipped" in w for w in res.warnings)


def test_bundle_entry_cap_degrades_not_rejects():
    # Over the entry cap: read the first N (sorted), list the rest honestly.
    data = _zip_bytes({f"f{i}.txt": "x" for i in range(8)})  # cap is 5
    res = extract_bundle("many.zip", data, CFG)
    assert len(res.meta["included"]) == 5
    assert res.meta["entries_omitted"] == 3
    assert res.meta["entry_count"] == 8
    assert any("3 more file(s)" in w for w in res.warnings)


def test_bundle_huge_entry_truncated_not_rejected():
    # An entry bigger than the text budget is included truncated, not bounced.
    cfg = dict(CFG, text_budget_chars=1000)
    data = _zip_bytes({"big.txt": "A" * (3 * 1024 * 1024), "small.txt": "tiny"})
    res = extract_bundle("big.zip", data, cfg)
    assert "big.txt" in res.meta["included"]
    assert res.meta["truncated"] is True
    assert "[... truncated" in res.text
    assert any("truncated" in w for w in res.warnings)
    # And the read stayed bounded: nowhere near 3 MB of "A"s survives.
    assert res.text.count("A") <= 1100


def test_bundle_zero_bytes_budget_rejected():
    # A 0-byte decompressed budget means the FIRST entry already has no budget,
    # so nothing is readable at all -> the whole bundle is the fatal case. (This
    # is the total-rejection path, NOT graceful names-only degradation.)
    cfg = dict(CFG, zip_max_decompressed_mb=0, text_budget_chars=400000)
    data = _zip_bytes({"a.txt": "aaaa", "b.txt": "bbbb"})
    with pytest.raises(AttachmentRejected) as exc:
        extract_bundle("zero.zip", data, cfg)
    assert "no readable files" in exc.value.message.lower()


def test_bundle_text_budget_lists_later_files_names_only():
    # Graceful degradation: the alphabetically-first entry eats the whole text
    # budget, so the later small entries are still READ into the manifest but
    # listed by name only (never opened) — the bundle is not aborted, and the
    # "these files exist but weren't read" signal reaches the model.
    cfg = dict(CFG, text_budget_chars=1000, zip_max_decompressed_mb=10)
    data = _zip_bytes({
        "a_big.txt": "A" * 4000,      # sorts first, consumes the 1000-char budget
        "b_small.txt": "bbbb",
        "c_small.txt": "cccc",
    })
    res = extract_bundle("proj.zip", data, cfg)
    assert res.meta["included"] == ["a_big.txt"]
    assert res.meta["names_only"] == ["b_small.txt", "c_small.txt"]
    assert any(
        "listed but not read (bundle text budget reached)" in w for w in res.warnings
    )


def test_declared_entry_count_reads_classic_eocd():
    from src.utils.attachment_bundle import _declared_entry_count
    # A real 3-entry zip declares 3 in its End-Of-Central-Directory record.
    data = _zip_bytes({"a.txt": "a", "b.txt": "b", "c.txt": "c"})
    assert _declared_entry_count(data) == 3
    # Hand-crafted EOCD (some prefix + a 22-byte record, total-entries=1234).
    eocd = struct.pack("<4sHHHHIIH", b"PK\x05\x06", 0, 0, 1234, 1234, 0, 0, 0)
    assert _declared_entry_count(b"\x00" * 40 + eocd) == 1234
    # Garbage / non-zip -> no EOCD found -> None (defer to zipfile).
    assert _declared_entry_count(b"not a zip at all") is None


def test_declared_entry_count_reads_zip64_eocd():
    from src.utils.attachment_bundle import _declared_entry_count
    # Classic EOCD count saturated at 0xFFFF -> the real count lives in the
    # Zip64 EOCD record reached via the Zip64 EOCD locator.
    z64 = struct.pack("<4sQHHIIQQQQ", b"PK\x06\x06", 44, 45, 45, 0, 0, 70000, 70000, 0, 0)
    loc = struct.pack("<4sIQI", b"PK\x06\x07", 0, 0, 1)
    classic = struct.pack(
        "<4sHHHHIIH", b"PK\x05\x06", 0, 0, 0xFFFF, 0xFFFF, 0xFFFFFFFF, 0xFFFFFFFF, 0
    )
    assert _declared_entry_count(z64 + loc + classic) == 70000


def test_bundle_rejects_when_declared_entry_count_over_cap(monkeypatch):
    # The EOCD pre-check fires BEFORE zipfile parses the central directory, so a
    # many-entry archive is bounced without materializing a ZipInfo per record.
    import src.utils.attachment_bundle as mod
    monkeypatch.setattr(mod, "_ENTRY_COUNT_HARD_CAP", 2)
    data = _zip_bytes({"a.txt": "a", "b.txt": "b", "c.txt": "c"})   # declares 3 > cap 2
    with pytest.raises(AttachmentRejected) as exc:
        extract_bundle("many.zip", data, dict(CFG, zip_max_entries=2))
    assert "limit" in exc.value.message.lower()


def test_bundle_with_no_readable_entries_rejected():
    data = _zip_bytes({"logo.png": "fakebytes"})
    with pytest.raises(AttachmentRejected) as exc:
        extract_bundle("pics.zip", data, CFG)
    assert "no readable files" in exc.value.message.lower()


def test_corrupt_zip_rejected():
    with pytest.raises(AttachmentRejected):
        extract_bundle("broken.zip", b"not a zip at all", CFG)


def test_read_entry_capped_stops_midstream():
    # Streaming cap holds even when metadata lied (2 MiB actual vs 1 MiB cap):
    # returns exactly cap bytes + truncated flag, never buffering the rest.
    from src.utils.attachment_bundle import _read_entry_capped
    cap = 1024 * 1024
    fh = io.BytesIO(b"Q" * (2 * 1024 * 1024))
    data, truncated = _read_entry_capped(fh, cap)
    assert truncated is True
    assert len(data) == cap
    assert fh.tell() <= cap + (1 << 20) + 1   # stopped reading right past the cap

    fh2 = io.BytesIO(b"Q" * 10)
    data2, truncated2 = _read_entry_capped(fh2, cap)
    assert (data2, truncated2) == (b"Q" * 10, False)


def test_bundle_nul_content_entry_skipped():
    data = _zip_bytes({"good.py": "x = 1", "blob.txt": "abc\x00def"})
    res = extract_bundle("mix.zip", data, CFG)
    assert res.meta["included"] == ["good.py"]
    reasons = {s["name"]: s["reason"] for s in res.meta["skipped"]}
    assert reasons["blob.txt"] == "not readable as text"
    assert "\x00" not in res.text


def test_bundle_corrupted_entry_skipped():
    # Store uncompressed, then flip a content byte -> CRC mismatch on read.
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr("only.txt", "HELLOHELLOHELLO")
    raw = bytearray(buf.getvalue())
    idx = raw.find(b"HELLOHELLOHELLO")
    raw[idx] = ord("X")
    with pytest.raises(AttachmentRejected) as exc:
        extract_bundle("broken.zip", bytes(raw), CFG)
    assert "no readable files" in exc.value.message.lower()


def test_bundle_entry_count_counts_all_entries():
    data = _zip_bytes({"a.py": "x", "logo.png": "fake", "inner.zip": "fake"})
    res = extract_bundle("proj.zip", data, CFG)
    assert res.meta["entry_count"] == 3          # total non-dir entries, incl. skipped
    assert res.meta["included"] == ["a.py"]


def test_corrupt_entry_consumes_budget():
    # Corrupt entry (declared ~0.9 MB) must charge the bytes budget so a
    # subsequent entry can only read what's left (1 MiB cap in CFG) —
    # corruption can't grant free decompression CPU.
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr("a-corrupt.txt", "H" * 900_000)
        zf.writestr("b-good.txt", "x" * 200_000)
    raw = bytearray(buf.getvalue())
    idx = raw.find(b"H" * 100)
    raw[idx] = ord("X")          # break a-corrupt.txt's CRC
    # Text budget above the entry sizes so reads reach EOF (zipfile only
    # verifies the CRC at EOF) and the bytes budget is the binding one.
    res = extract_bundle("amp.zip", bytes(raw), dict(CFG, text_budget_chars=2_000_000))
    reasons = {s["name"]: s["reason"] for s in res.meta["skipped"]}
    assert "unreadable" in reasons["a-corrupt.txt"]
    # b-good.txt declared 200_000 but only ~148k bytes of budget remained:
    # it comes back truncated, proving the corrupt entry was charged.
    assert "b-good.txt" in res.meta["included"]
    assert res.meta["truncated"] is True
    assert res.text.count("x") < 200_000


def test_bundle_sniffs_unknown_extensions():
    # The live-test failure: connections.conf.example was silently omitted.
    data = _zip_bytes({
        "config/connections.conf.example": "[DB]\nuser = archi_ro\n",
        "Dockerfile": "FROM python:3.12\n",
        "LICENSE": "MIT License\n",
        "blob.bin": b"\x00\x01\x02\x03" * 64,   # true binary still skipped
    })
    res = extract_bundle("proj.zip", data, CFG)
    assert "config/connections.conf.example" in res.meta["included"]
    assert "Dockerfile" in res.meta["included"]
    assert "LICENSE" in res.meta["included"]
    assert "archi_ro" in res.text and "FROM python" in res.text
    reasons = {s["name"]: s["reason"] for s in res.meta["skipped"]}
    assert reasons["blob.bin"] == "not readable as text"


def test_bundle_pdf_entry_gets_helpful_skip_reason():
    data = _zip_bytes({"code.py": "x = 1", "docs/manual.pdf": "%PDF-fake"})
    res = extract_bundle("proj.zip", data, CFG)
    reasons = {s["name"]: s["reason"] for s in res.meta["skipped"]}
    assert "attach the PDF" in reasons["docs/manual.pdf"]


def test_bundle_no_readable_rejection_names_the_reasons():
    data = _zip_bytes({"logo.png": "fakebytes", "inner.zip": "fakebytes"})
    with pytest.raises(AttachmentRejected) as exc:
        extract_bundle("pics.zip", data, CFG)
    assert "no readable files" in exc.value.message.lower()
    assert "logo.png" in exc.value.message      # says WHY, so users don't guess


def test_bundle_meta_has_full_entry_index():
    data = _zip_bytes({f"f{i}.txt": "x" for i in range(8)})  # processing cap is 5
    res = extract_bundle("many.zip", data, CFG)
    names = [e["name"] for e in res.meta["entries"]]
    assert len(names) == 8                      # index covers ALL entries, not just processed
    assert res.meta["entries_indexed"] == 8
    assert all(set(e) == {"name", "size", "kind"} for e in res.meta["entries"])


def test_bundle_entry_index_cap_warns(monkeypatch):
    # Over the index cap: entries beyond it are absent from the manifest index,
    # so the tools can't reach them — say so honestly. Shrink the cap to keep
    # the synthetic zip small.
    import src.utils.attachment_bundle as mod
    monkeypatch.setattr(mod, "_ENTRY_INDEX_CAP", 2)
    data = _zip_bytes({f"f{i}.txt": "x" for i in range(4)})   # 4 > cap 2
    res = extract_bundle("many.zip", data, CFG)
    assert res.meta["entries_indexed"] == 2
    assert len(res.meta["entries"]) == 2
    assert res.meta["entry_count"] == 4
    assert any(
        "entry index truncated to 2 of 4 entries" in w
        and "cannot be listed, read, or searched" in w
        for w in res.warnings
    )


def test_bundle_under_index_cap_no_cap_warning():
    data = _zip_bytes({"a.py": "x = 1", "b.py": "y = 2"})
    res = extract_bundle("proj.zip", data, CFG)
    assert not any("entry index truncated" in w for w in res.warnings)


def test_bundle_corrupt_deflate_entry_skipped_not_crash():
    # Corrupt bad.py's DEFLATE stream mid-entry (not near the tail, where a
    # broken checksum is just zipfile.BadZipFile, already caught below) so
    # zipfile raises a bare zlib.error while streaming the entry — same
    # construction technique as
    # test_attachment_reader.py::test_read_corrupted_entry_stream_is_friendly,
    # generalized to locate the SECOND entry's local file header by name
    # instead of assuming the corrupted entry starts at offset 0.
    import struct

    raw = bytearray(_zip_bytes({"good.py": "x = 1", "bad.py": "HELLO" * 2000}))
    idx = raw.find(b"bad.py")
    header_start = idx - 30
    namelen, extralen = struct.unpack("<HH", raw[header_start + 26: header_start + 30])
    data_start = header_start + 30 + namelen + extralen
    for i in range(data_start + 12, data_start + 18):
        raw[i] ^= 0xFF

    res = extract_bundle("mix.zip", bytes(raw), CFG)
    assert "good.py" in res.meta["included"]
    reasons = {s["name"]: s["reason"] for s in res.meta["skipped"]}
    assert reasons["bad.py"] == "unreadable entry (corrupted or encrypted)"


def test_bundle_entry_index_kinds():
    data = _zip_bytes({"a.py": "x", "logo.png": "fake", "doc.pdf": "%PDF", "inner.zip": "f", "r.docx": "f"})
    res = extract_bundle("proj.zip", data, CFG)
    kinds = {e["name"]: e["kind"] for e in res.meta["entries"]}
    assert kinds == {"a.py": "text", "logo.png": "image", "doc.pdf": "pdf",
                     "inner.zip": "nested-archive", "r.docx": "office"}


def test_declared_entry_count_reads_through_trailing_bytes():
    # zipfile locates the EOCD by scanning backward and tolerates arbitrary
    # bytes AFTER it, so the pre-parse entry-count guard must too. Requiring the
    # EOCD to be the file's exact tail let one trailing byte return None, which
    # silently skips the zip-bomb hard cap the guard exists to enforce.
    from src.utils.attachment_bundle import _declared_entry_count
    clean = _zip_bytes({f"f{i}.txt": "x" for i in range(12)})
    assert _declared_entry_count(clean) == 12
    assert _declared_entry_count(clean + b"\x00") == 12
    assert _declared_entry_count(clean + b"trailing junk after the eocd") == 12


def test_bundle_rejects_over_cap_even_with_trailing_bytes(monkeypatch):
    # End-to-end: the hard-cap reject must still fire when junk follows the EOCD,
    # otherwise appending one byte defeats the central-directory-bomb guard.
    import src.utils.attachment_bundle as mod
    monkeypatch.setattr(mod, "_ENTRY_COUNT_HARD_CAP", 2)
    data = _zip_bytes({"a.txt": "a", "b.txt": "b", "c.txt": "c"}) + b"\x00"
    with pytest.raises(AttachmentRejected) as exc:
        extract_bundle("many.zip", data, dict(CFG, zip_max_entries=2))
    assert "limit" in exc.value.message.lower()


def test_bundle_keeps_full_multibyte_entry_within_char_budget():
    # A file whose CHARACTER count fits text_budget_chars must be included whole.
    # The per-entry read cap is a byte read, so a character budget passed as the
    # byte cap truncated 3-byte UTF-8 text at ~1/3 even when it fit the budget.
    text = "ก" * 500                # Thai KO KAI: 500 chars, 1500 UTF-8 bytes
    data = _zip_bytes({"thai.txt": text})
    cfg = dict(CFG, text_budget_chars=600, zip_max_decompressed_mb=500,
               zip_max_entries=1000)
    res = extract_bundle("b.zip", data, cfg)
    assert "=== thai.txt ===" in res.text
    body = res.text.split("=== thai.txt ===\n", 1)[1]
    assert text in body                  # all 500 chars present, not ~1/3
    assert res.meta["truncated"] is False
    assert "[... truncated" not in res.text
