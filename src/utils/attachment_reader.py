"""Read-only reader core behind the agent attachment tools (spec 2026-07-07).

Pure functions over a conversation-scoped context: no Flask, no LangChain.
Every function returns a plain string (result or friendly error) and never
raises — tool output goes straight back to the model.
"""
from __future__ import annotations

import fnmatch
import io
import re
import zipfile
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

from src.utils.attachment_bundle import _entry_extension, _read_entry_capped
from src.utils.attachment_extraction import (
    HTML_EXTENSIONS,
    PDF_EXTENSIONS,
    _extract_html,
    _extract_pdf,
    sniff_text,
)
from src.utils.logging import get_logger

logger = get_logger(__name__)

_MAX_NAMES_IN_ERROR = 20
# Whole-document formats (PDF) can't be parsed from a byte-truncated prefix —
# the xref/trailer is at the end. Read them up to the upload-size ceiling
# (attachments cap at ~30 MB) rather than the smaller page-through byte cap,
# still bounded so a compression bomb can't run away.
_DOC_READ_MAX_BYTES = 32 * 1024 * 1024


@dataclass(frozen=True)
class AttachmentToolContext:
    conversation_id: int
    service: Any
    caps: Dict[str, int]


def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024.0
    return f"{n} B"


def _items(ctx: AttachmentToolContext, include_text: bool = True) -> List[dict]:
    # include_text=False skips streaming every attachment's full extracted_text
    # for callers that only read metadata (listing, name checks).
    return ctx.service.get_context_items(ctx.conversation_id, include_text=include_text) or []


def _known_names(ctx: AttachmentToolContext) -> List[str]:
    return [i.get("filename", "") for i in _items(ctx, include_text=False)]


def _duplicate_notice(items: List[dict], filename: str) -> str:
    """When several attachments in one conversation share `filename`, only the
    most recent is reachable by name (get_for_tools resolves newest-first) — say
    so, so an older same-named file isn't mistaken for the one being read and a
    miss isn't read as "that content isn't attached"."""
    dupes = sum(1 for i in items if i.get("filename", "") == filename)
    if dupes <= 1:
        return ""
    return (f'[note: {dupes} attachments here are named "{filename}"; showing the '
            f"most recent. The other {dupes - 1} same-named attachment(s) can't be "
            "read or searched by name.]\n")


def _not_found(kind: str, missing: str, names: List[str]) -> str:
    shown = ", ".join(names[:_MAX_NAMES_IN_ERROR])
    more = f" (+{len(names) - _MAX_NAMES_IN_ERROR} more)" if len(names) > _MAX_NAMES_IN_ERROR else ""
    return f'{kind} "{missing}" Not found. Valid names: {shown}{more}'


def _entry_matches(name: str, prefix: str, glob: str) -> bool:
    """Path filter shared by list_files. prefix is forgiving of a missing
    zip-root segment: "src/" matches "T0-master/src/a.py" via the "/src/"
    membership test. glob is a full fnmatch against the path."""
    if prefix and not (name.startswith(prefix) or ("/" + prefix) in name):
        return False
    if glob and not fnmatch.fnmatch(name, glob):
        return False
    return True


def list_files(ctx: AttachmentToolContext, prefix: str = "", glob: str = "") -> str:
    items = _items(ctx, include_text=False)   # listing renders metadata only
    if not items:
        return "This conversation has no attachments."
    prefix = (prefix or "").lstrip("/")
    glob = glob or ""
    filtering = bool(prefix or glob)
    cap = int(ctx.caps.get("list_max_chars", 40000))
    all_names = [item.get("filename", "") for item in items]
    dup_names = {n for n in all_names if n and all_names.count(n) > 1}

    total = 0            # K: every top-level filename plus every bundle entry
    matched = 0          # M: how many passed the filter
    body: List[str] = []
    for i, item in enumerate(items, start=1):
        meta = item.get("extraction_meta") or {}
        kind = item.get("kind", "document")
        filename = item.get("filename", "")
        entries = meta.get("entries") or []

        total += 1
        top_match = _entry_matches(filename, prefix, glob)
        if top_match:
            matched += 1

        entry_lines: List[str] = []
        for e in entries:
            total += 1
            ename = e.get("name", "")
            if filtering and not _entry_matches(ename, prefix, glob):
                continue
            matched += 1
            note = "" if e.get("kind") == "text" else f" [{e.get('kind')}]"
            entry_lines.append(f"   {ename} ({_human_size(int(e.get('size', 0)))}){note}")

        if filtering and not top_match and not entry_lines:
            continue
        dup = (f' [duplicate name — read/search reach only the most recent "{filename}"]'
               if filename in dup_names else "")
        body.append(f"{i}. {filename} — {kind}{dup}")
        body.extend(entry_lines)
        entry_count = int(meta.get("entry_count", len(entries)) or 0)
        if entry_count > len(entries):
            body.append(
                f"   [index truncated: only the first {len(entries)} of "
                f"{entry_count} entries are addressable]"
            )

    if filtering and matched == 0:
        missing = f'prefix "{prefix}"' if prefix else f'glob "{glob}"'
        return (f'No entries match {missing}. Call list_attachment_files '
                "with no filter to see every path.")
    header = f"attachments in this conversation ({len(items)}):"
    if filtering:
        label_parts: List[str] = []
        if prefix:
            label_parts.append(f'prefix="{prefix}"')
        if glob:
            label_parts.append(f'glob="{glob}"')
        header = (f"attachments in this conversation ({len(items)}): "
                  f"[filtered: {' '.join(label_parts)} — {matched} of {total} entries]")
    out = "\n".join([header] + body)
    if len(out) > cap:
        out = out[:cap] + "\n[listing truncated — narrow down with search_attachment_files]"
    return out


def _slice(text: str, offset: int, cap: int, truncated: bool = False) -> str:
    if not text:
        return "(file is empty — 0 chars)"
    if offset and offset >= len(text):
        return f"file is {len(text)} chars long; offset {offset} is past the end"
    piece = text[offset:offset + cap]
    end = offset + len(piece)
    if end >= len(text):
        # `truncated` means the entry was byte-capped on read, so this is NOT
        # the real end of the file — never claim "[end of file]" or the model
        # will report content past the cap as absent.
        tail = (
            f"[read cap reached at {end} chars — this entry is larger than the "
            "per-read byte limit, so the rest was not read and can't be paged to]"
            if truncated
            else "[end of file]"
        )
    else:
        tail = f"[truncated at {end} chars — continue with offset={end}]"
    return f"{piece}\n{tail}"


def _open_bundle_zip(row: dict) -> Tuple[Any, str]:
    """Open the stored zip once. Returns (ZipFile, "") on success or
    (None, error_msg). search_files opens each bundle's zip a single time and
    reuses it across all entries instead of reopening it per entry."""
    try:
        return zipfile.ZipFile(io.BytesIO(row.get("original_bytes") or b"")), ""
    except Exception as exc:
        logger.warning("attachment_reader: could not reopen zip for %s: %s", row.get("filename"), exc)
        return None, "The stored archive could not be reopened."


def _extract_entry_from_zip(ctx: AttachmentToolContext, row: dict, entry: str,
                            zf: zipfile.ZipFile, raw: bool = False) -> Tuple[str, bool, bool]:
    """Extract one entry from an ALREADY-OPEN ZipFile. Returns
    (text, is_error, byte_truncated). Callers must branch on is_error rather
    than sniffing the text — friendly-error strings and real file content can
    both contain the same substrings (e.g. "aren't", "unreadable"), so a
    content-based heuristic both over- and under-matches. byte_truncated is True
    only on the text path when the read hit the per-read byte cap, so callers can
    tell an honest "there's more" instead of claiming end-of-file. With raw=True
    the PDF/HTML extractors are skipped and the entry's stored bytes are returned
    verbatim via sniff_text."""
    meta = row.get("extraction_meta") or {}
    index = {e.get("name"): e for e in (meta.get("entries") or [])}
    names = list(index)
    if entry not in index:
        return _not_found("Entry", entry, names), True, False
    kind = index[entry].get("kind", "text")
    if kind == "image":
        return f'"{entry}" is an image — images aren\'t supported yet (text documents only for now).', True, False
    if kind == "office":
        return f'"{entry}" is an Office file — export it as PDF and attach that.', True, False
    if kind == "nested-archive":
        return f'"{entry}" is a nested archive — nested archives aren\'t unpacked.', True, False
    ext = _entry_extension(entry)
    read_cap = int(ctx.caps.get("read_max_bytes", 8 << 20))
    # A PDF is parsed whole (xref/trailer at the end), so the page-through byte
    # cap would leave it unreadable — read it up to the document ceiling. raw
    # mode returns bytes verbatim and keeps the paging cap.
    if not raw and (kind == "pdf" or ext in PDF_EXTENSIONS):
        read_cap = max(read_cap, _DOC_READ_MAX_BYTES)
    try:
        with zf.open(entry) as fh:
            raw_bytes, truncated = _read_entry_capped(fh, read_cap)
    except KeyError:
        return _not_found("Entry", entry, names), True, False
    except Exception as exc:
        # Broad on purpose: a corrupted DEFLATE stream can surface as
        # zipfile.BadZipFile, RuntimeError, a bare zlib.error, or even a
        # ValueError (bad seek) depending on exactly how the bytes are
        # damaged — never let entry content decide whether this raises.
        logger.warning("attachment_reader: entry read failed for %s:%s: %s", row.get("filename"), entry, exc)
        return f'"{entry}" is unreadable (corrupted or encrypted).', True, False
    if not raw:
        if kind == "pdf" or ext in PDF_EXTENSIONS:
            try:
                return _extract_pdf(raw_bytes, entry, 50).text, False, False
            except Exception as exc:
                logger.warning("attachment_reader: pdf extraction failed for %s:%s: %s", row.get("filename"), entry, exc)
                return f'Could not read "{entry}" as a PDF: {exc}', True, False
        if ext in HTML_EXTENSIONS:
            try:
                return _extract_html(raw_bytes), False, False
            except Exception as exc:
                logger.warning("attachment_reader: html parse failed for %s:%s: %s", row.get("filename"), entry, exc)
                return f'"{entry}" could not be parsed as HTML.', True, False
    text = sniff_text(raw_bytes, allow_truncated_tail=truncated)
    if text is None:
        return f'"{entry}" doesn\'t look like readable text — is it a binary file?', True, False
    return text, False, truncated


def _extract_entry(ctx: AttachmentToolContext, row: dict, entry: str, raw: bool = False) -> Tuple[str, bool, bool]:
    """read_file's single-read path: open the stored zip and extract one entry.
    search_files instead opens the zip once via _open_bundle_zip and reuses it
    across entries by calling _extract_entry_from_zip directly."""
    zf, err = _open_bundle_zip(row)
    if zf is None:
        return err, True, False
    with zf:
        return _extract_entry_from_zip(ctx, row, entry, zf, raw=raw)


def read_file(ctx: AttachmentToolContext, filename: str, entry: str = "",
              offset: int = 0, mode: str = "text") -> str:
    try:
        offset = max(0, int(offset))
    except (TypeError, ValueError):
        offset = 0
    if mode not in ("text", "raw"):
        mode = "text"
    row = ctx.service.get_for_tools(ctx.conversation_id, filename)
    if row is None:
        return _not_found("Attachment", filename, _known_names(ctx))
    notice = _duplicate_notice(_items(ctx, include_text=False), filename)   # counts names only
    cap = int(ctx.caps.get("read_max_chars", 20000))
    if row.get("kind") == "bundle" and entry:
        text, is_error, truncated = _extract_entry(ctx, row, entry, raw=(mode == "raw"))
        if is_error:
            return text        # error strings pass through un-sliced
        return notice + _slice(text, offset, cap, truncated=truncated)
    if row.get("kind") == "bundle" and not entry:
        return notice + ('This attachment is a bundle — pass entry="<path>" '
                "(see list_attachment_files for paths).")
    if mode == "raw":
        raw_text = sniff_text(row.get("original_bytes") or b"")
        if raw_text is None:
            return f'"{filename}" raw bytes are not decodable text.'
        return notice + _slice(raw_text, offset, cap)
    return notice + _slice(row.get("extracted_text") or "", offset, cap)


def search_files(ctx: AttachmentToolContext, query: str, regex: bool = False) -> str:
    if not query:
        return "Provide a non-empty query."
    if regex:
        try:
            pattern = re.compile(query, re.IGNORECASE)
        except re.error as exc:
            return f"Invalid regex: {exc}"
        # Runaway-regex guard: match per line, lines capped at 2000 chars.
        match = lambda line: pattern.search(line[:2000])  # noqa: E731
    else:
        lowered = query.lower()
        match = lambda line: lowered in line[:2000].lower()  # noqa: E731
    max_results = int(ctx.caps.get("search_max_results", 20))
    scan_budget = int(ctx.caps.get("read_max_bytes", 8 << 20))
    scanned_bytes = 0
    content_hits: List[str] = []      # in-file content matches — drive the result cap
    name_hits: List[str] = []         # filename/path matches — recorded, never starve content
    notes: List[str] = []             # informational lines — uncounted, appended last
    scanned_bundles: set = set()      # bundle names already searched (dedup by name)
    shadowed_bundles: List[str] = []  # older same-named bundles get_for_tools can't reach

    def _result() -> str:
        lines = content_hits + name_hits + notes
        if lines:
            return "\n".join(lines)
        return (f'No content matches for "{query}". Search scans file CONTENTS and '
                f'file NAMES; to explore a folder use list_attachment_files(prefix="<path>").')

    def _scan_one(label: str, text: str) -> bool:
        """Scan one already-extracted text for CONTENT matches, then charge its
        BYTE size against the scan budget. Both steps happen immediately for
        this text — before the caller is allowed to extract the next entry — so
        a bundle's later entries are never decompressed once the budget is
        already spent. Returns True once search_files should stop entirely
        (content-result cap or scan budget hit); the caller returns _result()
        in that case."""
        nonlocal scanned_bytes
        # scan_budget is a BYTE budget (read_max_bytes); charge bytes, not the
        # smaller character count, or multi-byte text scans ~3x past the cap.
        scanned_bytes += len(text.encode("utf-8"))
        for line_no, line in enumerate(text.splitlines(), start=1):
            if match(line):
                content_hits.append(f"{label}:{line_no}: {line.strip()[:200]}")
                if len(content_hits) >= max_results:
                    notes.append(f"[stopped at {max_results} matches — refine the query]")
                    return True
        if scanned_bytes > scan_budget:
            notes.append(
                f"[search stopped early after scanning ~{scanned_bytes} bytes — "
                "refine the query or read files directly]"
            )
            return True
        return False

    def _name_hit(label: str) -> None:
        """Record a filename/entry-path match (no content scan) so path-only
        queries like a folder name still surface. Capped on its own so path
        matches can't consume the content-result budget, and never stops the
        content scan of later entries."""
        if match(label) and len(name_hits) < max_results:
            name_hits.append(f"{label} (file name matches)")

    for item in _items(ctx):
        filename = item.get("filename", "")
        if item.get("kind") == "bundle":
            if filename in scanned_bundles:
                # get_for_tools only ever returns the newest same-named row, so
                # re-scanning would just re-open the same archive; the older
                # bundle is unreachable by name — record it and move on.
                if filename not in shadowed_bundles:
                    shadowed_bundles.append(filename)
                continue
            scanned_bundles.add(filename)
            row = ctx.service.get_for_tools(ctx.conversation_id, filename)
            meta = (row or {}).get("extraction_meta") or {}
            zf = None
            zf_failed = False
            try:
                for e in meta.get("entries") or []:
                    label = f"{filename}:{e.get('name')}"
                    _name_hit(label)
                    if e.get("kind") != "text":
                        continue    # non-text entries are name-searchable only
                    if zf is None and not zf_failed:
                        # Open the bundle's zip ONCE and reuse it for every
                        # entry, rather than reopening it per _extract_entry.
                        zf, _ = _open_bundle_zip(row or {})
                        zf_failed = zf is None
                    if zf is None:
                        continue
                    text, is_error, truncated = _extract_entry_from_zip(ctx, row, e.get("name"), zf)
                    if is_error:
                        continue    # friendly errors aren't scannable content
                    if _scan_one(label, text):
                        return _result()
                    if truncated:
                        notes.append(
                            f"[note: {label} is larger than the per-read limit — only its "
                            f"first ~{scan_budget} bytes were searched; a non-match there "
                            "is not conclusive]"
                        )
            finally:
                if zf is not None:
                    zf.close()
        else:
            _name_hit(filename)
            if _scan_one(filename, item.get("extracted_text") or ""):
                return _result()
    if shadowed_bundles:
        shown = ", ".join(f'"{n}"' for n in shadowed_bundles[:5])
        notes.append(
            f"[note: an older attachment shares a name with a newer one ({shown}); "
            "only the most recent of each name is searched.]"
        )
    return _result()
