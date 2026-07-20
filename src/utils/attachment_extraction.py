"""Extraction for conversation Attachments (spec 2026-07-06, v1 text-first).

Pure functions: bytes in, ExtractionResult out. No disk writes, no DB.
pypdf/bs4 are imported lazily (they ship in the chat image via
requirements-base.txt but are not pyproject dependencies).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import List, Optional, Tuple

# Text-family extensions decoded via _decode_text's ladder (UTF-16 BOM / NUL sniff / UTF-8 / latin-1 with control-density check).
# Union of loader_utils' TextLoader set and the code-file families the spec accepts.
TEXT_EXTENSIONS = {
    ".txt", ".md", ".rst", ".log", ".json", ".yaml", ".yml", ".csv", ".tsv",
    ".c", ".cpp", ".h", ".sh", ".php", ".py",
    ".js", ".ts", ".jsx", ".tsx", ".java", ".go", ".rs", ".toml",
}
HTML_EXTENSIONS = {".html", ".htm"}
PDF_EXTENSIONS = {".pdf"}
ZIP_EXTENSIONS = {".zip"}

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
OFFICE_EXTENSIONS = {".docx", ".xlsx", ".pptx", ".doc", ".xls", ".ppt", ".odt", ".rtf"}
ARCHIVE_EXTENSIONS = {".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar"}

_SCANNED_DOC_WARNING = (
    "This PDF looks scanned or image-based — I can barely read it. "
    "Only its extractable text is available."
)

_HTML_EMBEDDED_APPEND_CAP = 200_000  # cap on raw script/style text appended to text-poor HTML


class AttachmentRejected(Exception):
    """A validation/extraction failure with a user-facing message."""

    def __init__(self, message: str, http_status: int = 422):
        super().__init__(message)
        self.message = message
        self.http_status = http_status


@dataclass
class ExtractionResult:
    text: str
    kind: str                      # 'document' | 'bundle'
    meta: dict = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)


def accepted_extensions() -> set:
    return TEXT_EXTENSIONS | HTML_EXTENSIONS | PDF_EXTENSIONS | ZIP_EXTENSIONS


def _extension(filename: str) -> str:
    return PurePosixPath(filename.replace("\\", "/").lower()).suffix


_SAFE_EXTENSION_RE = re.compile(r"^\.[a-z0-9_+\-]{1,15}$")


def _safe_extension(ext: str) -> str:
    """Sniffed files carry arbitrary user-chosen suffixes; only let a tame
    charset through to stored metadata / UI chips."""
    return ext if _SAFE_EXTENSION_RE.match(ext) else ""


def _pdf_reader(data: bytes):
    """Seam for tests. Lazily imports pypdf (ships in the chat image)."""
    import io

    from pypdf import PdfReader

    return PdfReader(io.BytesIO(data))


def sniff_text(data: bytes, allow_truncated_tail: bool = False) -> Optional[str]:
    """Decode ladder shared by top-level files and bundle entries: text out,
    or None for binary. With allow_truncated_tail, a multi-byte character cut
    at EOF by a capped read is tolerated (drops at most the last 3 bytes)."""
    # 1) UTF-16 (BOM present): genuine UTF-16 text is accepted; anything
    #    BOM-prefixed that still fails to decode is binary junk.
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        try:
            return data.decode("utf-16")
        except UnicodeDecodeError:
            if allow_truncated_tail and len(data) % 2:
                try:
                    return data[:-1].decode("utf-16")
                except UnicodeDecodeError:
                    return None
            return None
    # 2) NUL bytes never appear in real text files (and Postgres TEXT
    #    cannot store them) — reject renamed binaries early.
    if b"\x00" in data:
        return None
    # 3) Normal path.
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        if allow_truncated_tail:
            for cut in (1, 2, 3):
                try:
                    return data[:-cut].decode("utf-8")
                except UnicodeDecodeError:
                    continue
    # 4) Legacy single-byte text (latin-1 always decodes) — accept only if
    #    it looks like text: low density of C0/C1/DEL control characters.
    text = data.decode("latin-1")
    sample = text[:8192]
    control = sum(
        1
        for ch in sample
        if (ord(ch) < 32 or 0x7F <= ord(ch) <= 0x9F) and ch not in "\t\n\r\f\v"
    )
    if sample and control / len(sample) > 0.10:
        return None
    return text


def _decode_text(data: bytes, filename: str) -> str:
    text = sniff_text(data)
    if text is None:
        _reject_binary(filename)
    return text


def _reject_binary(filename: str):
    raise AttachmentRejected(
        f'"{filename}" doesn\'t look like readable text — is it a binary file?', 422
    )


def _extract_html_with_meta(data: bytes) -> Tuple[str, List[str]]:
    """Visible-text extraction with a text-poor fallback: single-file apps hide
    their whole payload in a <script> literal, so when visible page text is
    negligible relative to the file size we append the raw <script>/<style>
    source (never JSON-parsed) and record a warning that the page was text-poor."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(data, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    visible = soup.get_text(separator="\n")

    warnings: List[str] = []
    visible_len = len(visible.strip())
    if visible_len < max(200, 0.15 * len(data)):
        raw_soup = BeautifulSoup(data, "html.parser")
        raw_parts = [t.get_text() for t in raw_soup(["script", "style"])]
        embedded = "\n\n".join(p for p in raw_parts if p.strip())[:_HTML_EMBEDDED_APPEND_CAP]
        if embedded.strip():
            pct = (visible_len / len(data) * 100) if data else 0.0
            visible = (
                f"{visible}\n\n[embedded <script>/<style> content — raw, appended "
                f"because visible page text is only {pct:.1f}% of the file]\n{embedded}"
            )
            warnings.append(
                f"only {pct:.1f}% of this HTML file is visible page text; "
                "embedded script/style content was appended raw"
            )
    return visible, warnings


def _extract_html(data: bytes) -> str:
    return _extract_html_with_meta(data)[0]


def _extract_pdf(data: bytes, filename: str, text_poor_page_chars: int) -> ExtractionResult:
    try:
        reader = _pdf_reader(data)
    except Exception as exc:
        raise AttachmentRejected(f'Could not read "{filename}" as a PDF: {exc}', 422)
    if getattr(reader, "is_encrypted", False):
        raise AttachmentRejected(
            f'"{filename}" is password-protected — remove the password and re-attach.', 422
        )

    page_texts: List[str] = []
    text_poor_pages: List[int] = []
    for i, page in enumerate(reader.pages, start=1):
        try:
            page_text = page.extract_text() or ""
        except Exception:
            page_text = ""
        page_texts.append(page_text)
        if len(page_text.strip()) < text_poor_page_chars:
            text_poor_pages.append(i)

    # Page markers so "what's on page 5?" works (ChatGPT/Claude parity).
    text = "\n\n".join(
        f"--- Page {i} ---\n{t.strip()}" for i, t in enumerate(page_texts, start=1)
    ).strip()
    raw_chars = sum(len(t.strip()) for t in page_texts)   # warnings judge content, not markers
    page_count = len(page_texts)
    warnings: List[str] = []
    if page_count == 0 or len(text_poor_pages) * 2 >= page_count or raw_chars < 500:
        warnings.append(_SCANNED_DOC_WARNING)
    elif text_poor_pages:
        pages = ", ".join(str(p) for p in text_poor_pages)
        warnings.append(f"Pages {pages} have little readable text.")

    meta = {"extension": ".pdf", "page_count": page_count, "text_poor_pages": text_poor_pages}
    return ExtractionResult(text=text, kind="document", meta=meta, warnings=warnings)


def extract_attachment(filename: str, data: bytes, config: dict) -> ExtractionResult:
    """Validate and extract one attached file. Raises AttachmentRejected."""
    ext = _extension(filename)

    max_bytes = int(config.get("max_file_mb", 30)) * 1024 * 1024
    if len(data) > max_bytes:
        raise AttachmentRejected(
            f'"{filename}" is larger than the {config.get("max_file_mb", 30)} MB limit.', 413
        )

    if ext in OFFICE_EXTENSIONS:
        raise AttachmentRejected(
            f'Word/Excel/PowerPoint files aren\'t supported yet — export it as PDF and attach that.', 415
        )
    if ext in IMAGE_EXTENSIONS:
        raise AttachmentRejected("Images aren't supported yet — text documents only for now.", 415)
    if ext in ARCHIVE_EXTENSIONS:
        raise AttachmentRejected(
            "Only .zip archives are supported — re-pack the files as .zip or attach them directly.", 415
        )

    if ext in ZIP_EXTENSIONS:
        from src.utils.attachment_bundle import extract_bundle  # Task 3

        return extract_bundle(filename, data, config)

    if ext in PDF_EXTENSIONS:
        return _extract_pdf(data, filename, int(config.get("text_poor_page_chars", 50)))

    if ext in HTML_EXTENSIONS:
        text, warnings = _extract_html_with_meta(data)
        return ExtractionResult(text=text, kind="document", meta={"extension": ext}, warnings=warnings)

    if ext in TEXT_EXTENSIONS:
        text = _decode_text(data, filename)
        return ExtractionResult(text=text, kind="document", meta={"extension": ext}, warnings=[])

    # Unknown (or no) extension: accept anything that reads as text —
    # .conf/.example/Dockerfile-style files are first-class on Claude/ChatGPT.
    text = sniff_text(data)
    if text is None:
        raise AttachmentRejected(
            f'"{filename}" doesn\'t read as text. Supported: PDF, HTML, .zip bundles, '
            f"and any plain-text or code file.",
            415,
        )
    return ExtractionResult(
        text=text, kind="document", meta={"extension": _safe_extension(ext)}, warnings=[]
    )
