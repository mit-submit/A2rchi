"""Unit tests for attachment extraction (documents; bundles are in test_attachment_bundle.py)."""
import pytest

from src.utils.attachment_extraction import (
    AttachmentRejected,
    ExtractionResult,
    extract_attachment,
    accepted_extensions,
)

CFG = {
    "max_file_mb": 30,
    "text_poor_page_chars": 50,
    "zip_max_decompressed_mb": 500,
    "zip_max_entries": 1000,
    "text_budget_chars": 400000,
}


# --- happy paths -------------------------------------------------------------

def test_plain_text_extracts():
    res = extract_attachment("notes.txt", b"hello attachment", CFG)
    assert isinstance(res, ExtractionResult)
    assert res.kind == "document"
    assert res.text == "hello attachment"
    assert res.warnings == []
    assert res.meta["extension"] == ".txt"


def test_code_file_extracts():
    res = extract_attachment("script.py", b"print('hi')\n", CFG)
    assert res.text == "print('hi')\n"


def test_html_extracts_text_only():
    # Text-rich page: visible prose is well above the fallback threshold, so
    # the <script> stays stripped (text-poor pages re-append it instead — see
    # test_text_poor_html_appends_script_source).
    body = "".join(
        f"<p>Paragraph {i}: real visible prose, plenty of readable words here.</p>"
        for i in range(20)
    )
    html = (
        f"<html><body><h1>Title</h1>{body}<p>Body text</p>"
        "<script>bad()</script></body></html>"
    ).encode()
    res = extract_attachment("page.html", html, CFG)
    assert "Title" in res.text and "Body text" in res.text
    assert "bad()" not in res.text  # scripts stripped
    assert res.warnings == []


def test_text_poor_html_appends_script_source():
    # Live-test bug: a 48 KB single-file app extracted to <2% visible text
    # because its whole curriculum lived in one <script> literal that
    # _extract_html decomposed away, and nothing warned about it.
    script = "const PLAN = {course: 'CURRICULUM_TOKEN_XYZ', lessons: 42};" + "// pad\n" * 2000
    html = f"<html><body><p>Loading...</p><script>{script}</script></body></html>".encode()
    res = extract_attachment("app.html", html, CFG)
    assert "CURRICULUM_TOKEN_XYZ" in res.text                 # script source recovered
    assert "embedded <script>/<style> content" in res.text   # framed marker present
    assert any("visible page text" in w and "appended raw" in w for w in res.warnings)


def test_text_poor_html_without_scripts_no_spurious_warning():
    # A short page with no <script>/<style> has nothing to append: no warning,
    # no marker — the fallback must not fire on merely-small documents.
    res = extract_attachment("tiny.html", b"<html><body><p>hi there</p></body></html>", CFG)
    assert "hi there" in res.text
    assert "embedded <script>/<style> content" not in res.text
    assert res.warnings == []


def test_pdf_extracts_pages_and_detects_text_poor(monkeypatch):
    # Unit-level: fake pypdf pages (2 rich, 2 empty) instead of building a real PDF.
    class FakePage:
        def __init__(self, text): self._t = text
        def extract_text(self): return self._t

    class FakeReader:
        is_encrypted = False
        pages = [FakePage("x" * 500), FakePage("y" * 500), FakePage(""), FakePage("")]

    import src.utils.attachment_extraction as mod
    monkeypatch.setattr(mod, "_pdf_reader", lambda data: FakeReader())
    res = extract_attachment("doc.pdf", b"%PDF-fake", CFG)
    assert res.meta["page_count"] == 4
    assert res.meta["text_poor_pages"] == [3, 4]          # 1-indexed
    # 2/4 pages poor => >=50% => document-level scanned warning
    assert any("barely read" in w for w in res.warnings)


def test_pdf_mostly_rich_gets_page_warnings_not_doc_warning(monkeypatch):
    class FakePage:
        def __init__(self, text): self._t = text
        def extract_text(self): return self._t

    class FakeReader:
        is_encrypted = False
        pages = [FakePage("x" * 500)] * 3 + [FakePage("")]

    import src.utils.attachment_extraction as mod
    monkeypatch.setattr(mod, "_pdf_reader", lambda data: FakeReader())
    res = extract_attachment("doc.pdf", b"%PDF-fake", CFG)
    assert res.meta["text_poor_pages"] == [4]
    assert not any("barely read" in w for w in res.warnings)


def test_pdf_pages_get_page_markers(monkeypatch):
    # Page-numbered extraction so "what's on page 2?" works like Claude/ChatGPT.
    class FakePage:
        def __init__(self, text): self._t = text
        def extract_text(self): return self._t

    class FakeReader:
        is_encrypted = False
        pages = [FakePage("alpha " * 20), FakePage("bravo " * 20)]

    import src.utils.attachment_extraction as mod
    monkeypatch.setattr(mod, "_pdf_reader", lambda data: FakeReader())
    res = extract_attachment("doc.pdf", b"%PDF-fake", CFG)
    assert "--- Page 1 ---" in res.text and "--- Page 2 ---" in res.text
    assert res.text.index("--- Page 2 ---") > res.text.index("alpha")
    assert "bravo" in res.text.split("--- Page 2 ---", 1)[1]


def test_sparse_slide_deck_not_flagged_as_scanned(monkeypatch):
    # Slide decks average ~100-300 chars/page; that is sparse but NOT scanned.
    class FakePage:
        def __init__(self, text): self._t = text
        def extract_text(self): return self._t

    class FakeReader:
        is_encrypted = False
        pages = [FakePage("Slide title and a couple of short bullet points here." * 3)] * 10

    import src.utils.attachment_extraction as mod
    monkeypatch.setattr(mod, "_pdf_reader", lambda data: FakeReader())
    res = extract_attachment("slides.pdf", b"%PDF-fake", CFG)
    assert res.warnings == []


# --- decode ladder & edge cases -----------------------------------------------

def test_utf16_text_accepted():
    res = extract_attachment("win.txt", "hello attachment".encode("utf-16"), CFG)
    assert res.text == "hello attachment"


def test_binary_with_nul_bytes_rejected():
    data = b"MZ\x90\x00\x03\x00\x00\x00\x04\x00" * 20  # PE-header-ish
    with pytest.raises(AttachmentRejected) as exc:
        extract_attachment("app.txt", data, CFG)
    assert exc.value.http_status == 422
    assert "binary" in exc.value.message.lower()


def test_nul_free_c1_binary_rejected():
    # No NUL, no BOM, invalid UTF-8, entirely C1-control bytes: must reject.
    data = bytes([0x81, 0x82, 0x83, 0x84, 0x85, 0x86, 0x87, 0x88, 0x8B, 0x8E, 0x8F, 0x90]) * 40
    with pytest.raises(AttachmentRejected) as exc:
        extract_attachment("weird.txt", data, CFG)
    assert exc.value.http_status == 422
    assert "binary" in exc.value.message.lower()


def test_legacy_latin1_text_accepted():
    res = extract_attachment("cafe.txt", "café con leche".encode("latin-1"), CFG)
    assert "café" in res.text


def test_zero_page_pdf_warns(monkeypatch):
    class FakeReader:
        is_encrypted = False
        pages = []

    import src.utils.attachment_extraction as mod
    monkeypatch.setattr(mod, "_pdf_reader", lambda data: FakeReader())
    res = extract_attachment("empty.pdf", b"%PDF-fake", CFG)
    assert any("barely read" in w for w in res.warnings)


# --- rejections --------------------------------------------------------------

@pytest.mark.parametrize("name,expected_snippet,status", [
    ("report.docx", "export it as PDF", 415),
    ("sheet.xlsx", "export it as PDF", 415),
    ("photo.png", "Images aren't supported yet", 415),
    ("clip.gif", "Images aren't supported yet", 415),
    ("data.tar.gz", "Only .zip archives", 415),
    ("data.7z", "Only .zip archives", 415),
])
def test_friendly_rejections(name, expected_snippet, status):
    with pytest.raises(AttachmentRejected) as exc:
        extract_attachment(name, b"irrelevant", CFG)
    assert expected_snippet in exc.value.message
    assert exc.value.http_status == status


# --- unknown extensions: sniff, don't allowlist (Claude/ChatGPT parity) --------

@pytest.mark.parametrize("name", [
    "connections.conf.example",   # the exact file archi omitted in live testing
    "app.conf",
    "Dockerfile",                 # no extension at all
    ".gitignore",
    "settings.ini",
])
def test_unknown_extension_text_accepted(name):
    res = extract_attachment(name, b"[db]\nuser = archi_ro\n", CFG)
    assert res.kind == "document"
    assert "archi_ro" in res.text


def test_unknown_extension_binary_rejected():
    data = b"MZ\x90\x00\x03\x00\x00\x00\x04\x00" * 20  # PE-header-ish
    with pytest.raises(AttachmentRejected) as exc:
        extract_attachment("virus.exe", data, CFG)
    assert exc.value.http_status == 415
    assert "read as text" in exc.value.message


def test_sniffed_extension_sanitized_in_meta():
    # Odd suffixes must not flow raw into stored metadata / UI chips.
    res = extract_attachment("notes.<b>", b"plain text", CFG)
    assert res.meta["extension"] == ""
    res2 = extract_attachment("app.conf", b"plain text", CFG)
    assert res2.meta["extension"] == ".conf"


def test_oversized_rejected():
    cfg = dict(CFG, max_file_mb=1)
    with pytest.raises(AttachmentRejected) as exc:
        extract_attachment("big.txt", b"x" * (1024 * 1024 + 1), cfg)
    assert exc.value.http_status == 413


def test_encrypted_pdf_rejected(monkeypatch):
    class FakeReader:
        is_encrypted = True
        pages = []

    import src.utils.attachment_extraction as mod
    monkeypatch.setattr(mod, "_pdf_reader", lambda data: FakeReader())
    with pytest.raises(AttachmentRejected) as exc:
        extract_attachment("locked.pdf", b"%PDF-fake", CFG)
    assert exc.value.http_status == 422
    assert "password" in exc.value.message.lower()


def test_undecodable_text_rejected():
    with pytest.raises(AttachmentRejected) as exc:
        extract_attachment("blob.txt", b"\xff\xfe" + bytes(range(256)) * 4, CFG)
    assert exc.value.http_status == 422


def test_accepted_extensions_include_core_set():
    exts = accepted_extensions()
    for e in (".pdf", ".txt", ".md", ".py", ".html", ".zip", ".go", ".ts"):
        assert e in exts
