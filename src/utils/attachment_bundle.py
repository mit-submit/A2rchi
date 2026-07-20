"""Zip -> Bundle extraction. In-memory only (no disk writes => no zip-slip).

A Bundle is ONE Attachment whose text stitches the archive's readable entries
with `=== path ===` headers (spec §7). Nothing is fatal except an unreadable
zip or one with no readable entries — budgets degrade gracefully instead
(Claude/ChatGPT parity: a big zip is read as far as budgets allow and the
rest is listed honestly):

- entries beyond ``zip_max_entries`` are counted but never opened;
- reads stream in 1 MiB chunks against a decompressed-bytes budget
  (``zip_max_decompressed_mb``) that corrupt entries still get charged for
  (CPU-amplification guard);
- included text is capped near the injection budget (``text_budget_chars``)
  because content beyond it could never reach the model anyway — oversized
  files come back truncated with an inline marker, and once the budget is
  spent the remaining readable files are listed by name only.
"""
from __future__ import annotations

import io
import zipfile
from typing import List, Tuple

from src.utils.attachment_extraction import (
    ARCHIVE_EXTENSIONS,
    AttachmentRejected,
    ExtractionResult,
    IMAGE_EXTENSIONS,
    OFFICE_EXTENSIONS,
    PDF_EXTENSIONS,
    ZIP_EXTENSIONS,
    sniff_text,
)

_CHUNK_BYTES = 1 << 20  # stream zip entries in 1 MiB chunks
_TRUNCATION_MARKER = "\n[... truncated: file continues beyond the bundle text budget]"
_ENTRY_INDEX_CAP = 5000  # max entries listed in the manifest index (the tool-readable set)

_EOCD_SIGNATURE = b"PK\x05\x06"
_ZIP64_LOCATOR_SIGNATURE = b"PK\x06\x07"
_ZIP64_EOCD_SIGNATURE = b"PK\x06\x06"
# A 30 MB upload can pack hundreds of thousands of empty central-directory
# records; zipfile.ZipFile materializes EVERY one as a ZipInfo at construction
# (hundreds of MB of transient RAM) BEFORE zip_max_entries is ever consulted.
# Refuse to even open an archive whose EOCD declares more entries than this so
# the parse can't amplify. Sits far above _ENTRY_INDEX_CAP so graceful
# degradation of real many-file zips is untouched.
_ENTRY_COUNT_HARD_CAP = 100_000


def _read_entry_capped(fh, cap: int) -> Tuple[bytes, bool]:
    """Read at most `cap` bytes from a file-like; returns (data, truncated).
    Never buffers more than cap + 1 MiB even when zip metadata lies about
    sizes — the truncated flag means the entry had more to give."""
    chunks: List[bytes] = []
    got = 0
    while got <= cap:
        chunk = fh.read(min(_CHUNK_BYTES, cap - got + 1))
        if not chunk:
            return b"".join(chunks), False
        got += len(chunk)
        chunks.append(chunk)
    return b"".join(chunks)[:cap], True


def _entry_extension(name: str) -> str:
    lowered = name.lower()
    dot = lowered.rfind(".")
    return lowered[dot:] if dot != -1 else ""


def _zip64_entry_count(data: bytes, eocd_offset: int) -> int | None:
    """Follow the Zip64 EOCD locator (immediately before the classic EOCD) to
    the Zip64 EOCD record and read its 8-byte total-entries field. None when the
    locator/record is absent or malformed."""
    loc = eocd_offset - 20
    if loc < 0 or data[loc:loc + 4] != _ZIP64_LOCATOR_SIGNATURE:
        return None
    z64 = int.from_bytes(data[loc + 8:loc + 16], "little")
    if z64 < 0 or z64 + 40 > len(data) or data[z64:z64 + 4] != _ZIP64_EOCD_SIGNATURE:
        return None
    return int.from_bytes(data[z64 + 32:z64 + 40], "little")


def _declared_entry_count(data: bytes) -> int | None:
    """Read the "total entries" field out of a zip's End-Of-Central-Directory
    record WITHOUT parsing the central directory itself. Returns the declared
    count, or None when no well-formed EOCD is found (leave that verdict to
    zipfile). Resolves the Zip64 EOCD when the classic 16-bit count is saturated
    (0xFFFF). Cheap tail scan so the entry-count guard can reject BEFORE
    zipfile materializes one ZipInfo per record."""
    if len(data) < 22:
        return None
    window_start = max(0, len(data) - (22 + 0xFFFF))  # EOCD + max comment length
    search = data[window_start:]
    pos = search.rfind(_EOCD_SIGNATURE)
    while pos != -1:
        record = search[pos:]
        if len(record) >= 22:
            comment_len = int.from_bytes(record[20:22], "little")
            # `>=`, not `==`: `record` runs to end-of-data, so any bytes after
            # the EOCD comment make an exact match fail. zipfile finds the EOCD
            # by the same backward scan and ignores trailing bytes, so a strict
            # match would blind this guard on archives zipfile still opens.
            if len(record) >= 22 + comment_len:
                count = int.from_bytes(record[10:12], "little")
                if count == 0xFFFF:
                    z64 = _zip64_entry_count(data, window_start + pos)
                    if z64 is not None:
                        return z64
                return count
        pos = search.rfind(_EOCD_SIGNATURE, 0, pos)
    return None


def extract_bundle(filename: str, data: bytes, config: dict) -> ExtractionResult:
    max_entries = int(config.get("zip_max_entries", 1000))
    bytes_budget = int(config.get("zip_max_decompressed_mb", 500)) * 1024 * 1024
    chars_budget = int(config.get("text_budget_chars", 400000))

    # Reject a central-directory bomb from its declared entry count BEFORE
    # zipfile parses (and materializes a ZipInfo for) every record. The hard cap
    # can only rise with an admin-raised zip_max_entries, never fall below it.
    entry_hard_cap = max(_ENTRY_COUNT_HARD_CAP, max_entries)
    declared_entries = _declared_entry_count(data)
    if declared_entries is not None and declared_entries > entry_hard_cap:
        raise AttachmentRejected(
            f'"{filename}" declares {declared_entries} entries, over the '
            f"{entry_hard_cap}-file limit — repack with fewer files or attach "
            "the ones you need directly.",
            422,
        )

    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except Exception:
        raise AttachmentRejected(f'"{filename}" isn\'t a readable zip archive.', 422)

    with zf:
        entries = sorted(
            (zi for zi in zf.infolist() if not zi.is_dir()),
            key=lambda z: z.filename,
        )
        entries_omitted = max(0, len(entries) - max_entries)

        def _entry_kind(name: str) -> str:
            ext = _entry_extension(name)
            if ext in ZIP_EXTENSIONS or ext in ARCHIVE_EXTENSIONS:
                return "nested-archive"
            if ext in IMAGE_EXTENSIONS:
                return "image"
            if ext in OFFICE_EXTENSIONS:
                return "office"
            if ext in PDF_EXTENSIONS:
                return "pdf"
            return "text"

        entry_index = [
            {
                "name": zi.filename.replace("\x00", ""),
                "size": zi.file_size,
                "kind": _entry_kind(zi.filename.replace("\x00", "")),
            }
            for zi in entries[:_ENTRY_INDEX_CAP]
        ]

        included: List[str] = []
        skipped: List[dict] = []
        names_only: List[str] = []   # readable-looking, but no budget left to open them
        parts: List[str] = []
        truncated_any = False
        bytes_read = 0
        chars_kept = 0
        for zi in entries[:max_entries]:
            name = zi.filename.replace("\x00", "")
            ext = _entry_extension(name)
            if ext in ZIP_EXTENSIONS or ext in ARCHIVE_EXTENSIONS:
                skipped.append({"name": name, "reason": "nested archives aren't unpacked"})
                continue
            if ext in IMAGE_EXTENSIONS:
                skipped.append({"name": name, "reason": "image — attach images directly (not supported yet)"})
                continue
            if ext in OFFICE_EXTENSIONS:
                skipped.append({"name": name, "reason": "Office file — export as PDF"})
                continue
            if ext in PDF_EXTENSIONS:
                skipped.append({"name": name, "reason": "PDF inside a zip — attach the PDF file directly to read it"})
                continue

            bytes_left = bytes_budget - bytes_read
            chars_left = chars_budget - chars_kept
            if bytes_left <= 0 or chars_left <= 0:
                names_only.append(name)
                continue
            # Everything else gets sniffed: text of ANY extension is included
            # (.conf/.example/Dockerfile/LICENSE...), binaries are skipped.
            try:
                with zf.open(zi) as fh:
                    # chars_left is a CHARACTER budget but _read_entry_capped
                    # counts BYTES. UTF-8 is up to 4 bytes/char, so read up to
                    # 4x the char budget (still bounded by bytes_left) to avoid
                    # truncating multi-byte text that fits the char budget; the
                    # char cap is re-applied to `content` below.
                    byte_cap = min(bytes_left, chars_left * 4 + 64)
                    raw, entry_truncated = _read_entry_capped(fh, byte_cap)
            # zipfile surfaces corrupt streams inconsistently (BadZipFile, RuntimeError, bare zlib.error, ValueError) — treat any read failure as an unreadable entry; the declared size is still charged below.
            except Exception:
                # Charge the declared size so corrupt entries can't grant
                # unlimited decompression budget (CPU-amplification guard).
                bytes_read += zi.file_size
                skipped.append({"name": name, "reason": "unreadable entry (corrupted or encrypted)"})
                continue
            bytes_read += len(raw)
            content = sniff_text(raw, allow_truncated_tail=entry_truncated)
            if content is None:
                skipped.append({"name": name, "reason": "not readable as text"})
                continue
            keep = min(len(content), chars_left)
            if keep < len(content) or entry_truncated:
                truncated_any = True
                content = content[:keep] + _TRUNCATION_MARKER
            else:
                content = content[:keep]
            chars_kept += keep
            included.append(name)
            parts.append(f"=== {name} ===\n{content}")

    if not included:
        detail = "; ".join(f"{s['name']}: {s['reason']}" for s in skipped[:3])
        suffix = f" ({detail})" if detail else ""
        raise AttachmentRejected(
            f'"{filename}" contains no readable files — attach text/code files '
            f"or a zip of them.{suffix}",
            422,
        )

    warnings: List[str] = []
    if skipped:
        summary = ", ".join(s["name"] for s in skipped[:5])
        more = f" (+{len(skipped) - 5} more)" if len(skipped) > 5 else ""
        warnings.append(f"{len(skipped)} file(s) skipped: {summary}{more}")
    if names_only:
        summary = ", ".join(names_only[:5])
        more = f" (+{len(names_only) - 5} more)" if len(names_only) > 5 else ""
        warnings.append(
            f"{len(names_only)} file(s) listed but not read (bundle text budget reached): {summary}{more}"
        )
    if entries_omitted:
        warnings.append(
            f"{entries_omitted} more file(s) beyond the {max_entries}-entry limit were not read."
        )
    if truncated_any:
        warnings.append("Some files were truncated to fit the bundle text budget.")
    if len(entries) > _ENTRY_INDEX_CAP:
        warnings.append(
            f"entry index truncated to {_ENTRY_INDEX_CAP} of {len(entries)} entries — "
            "files beyond the index cannot be listed, read, or searched"
        )

    meta = {
        "extension": ".zip",
        "entry_count": len(entries),
        "entries": entry_index,
        "entries_indexed": len(entry_index),
        "included": included,
        "skipped": skipped,
        "names_only": names_only,
        "entries_omitted": entries_omitted,
        "truncated": truncated_any,
    }
    return ExtractionResult(text="\n\n".join(parts), kind="bundle", meta=meta, warnings=warnings)
