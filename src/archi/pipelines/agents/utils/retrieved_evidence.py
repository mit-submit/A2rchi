"""Helpers for compact retrieved-context evidence payloads."""

from __future__ import annotations

import hashlib
import mimetypes
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from langchain_core.documents import Document


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff", ".tif", ".webp"}
TEXT_EXTENSIONS = {
    ".txt",
    ".md",
    ".rst",
    ".json",
    ".yaml",
    ".yml",
    ".csv",
    ".tsv",
    ".html",
    ".htm",
    ".log",
    ".py",
    ".c",
    ".cpp",
    ".h",
}
MAX_EXCERPT_CHARS = 1200


def build_retrieved_evidence_payload(documents: Iterable[Document]) -> Dict[str, Any]:
    """Return deduplicated evidence items grouped by source file."""
    items: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for index, doc in enumerate(documents, start=1):
        item = evidence_item_from_document(doc, rank=index)
        key = item.get("dedupe_key")
        if key in seen:
            continue
        seen.add(key)
        items.append(item)

    groups_by_hash: Dict[str, Dict[str, Any]] = {}
    for item in items:
        source = item["source"]
        resource_hash = source["resource_hash"]
        group = groups_by_hash.setdefault(
            resource_hash,
            {
                "resource_hash": resource_hash,
                "display_name": source["display_name"],
                "source_type": source.get("source_type"),
                "file_kind": source.get("file_kind"),
                "mime_type": source.get("mime_type"),
                "source_url": source.get("source_url"),
                "download_available": bool(item.get("actions", {}).get("download_url")),
                "items": [],
            },
        )
        group["items"].append(item)

    return {
        "version": 1,
        "groups": list(groups_by_hash.values()),
        "items": items,
    }


def evidence_item_from_document(doc: Document, *, score: Optional[float] = None, rank: Optional[int] = None) -> Dict[str, Any]:
    metadata = dict(doc.metadata or {})
    resource_hash = _string(metadata.get("resource_hash")) or _fallback_hash(metadata, doc.page_content)
    display_name = (
        _string(metadata.get("display_name"))
        or _string(metadata.get("source_file"))
        or _string(metadata.get("filename"))
        or _string(metadata.get("title"))
        or resource_hash
    )
    source_path = (
        _string(metadata.get("path"))
        or _string(metadata.get("file_path"))
        or _string(metadata.get("source"))
        or _string(metadata.get("source_file"))
    )
    suffix = _suffix(metadata, display_name, source_path)
    file_kind = _file_kind(suffix, metadata)
    page = _page_metadata(metadata)
    chunk_source = _string(metadata.get("chunk_source")) or _string(metadata.get("source_kind"))
    kind = _evidence_kind(file_kind, chunk_source, page)
    explicit_score = score if score is not None else metadata.get("retriever_score")
    normalized_score = _number(explicit_score)
    excerpt = _excerpt(doc.page_content)
    unit_id = _unit_id(metadata, rank, page, excerpt, kind)
    preview = _preview_info(kind, page)
    item_id = _stable_id(resource_hash, unit_id, kind)

    return {
        "id": item_id,
        "dedupe_key": f"{resource_hash}:{unit_id}",
        "kind": kind,
        "rank": rank,
        "score": normalized_score,
        "excerpt": excerpt,
        "page": page,
        "retrieved_unit": {
            "id": unit_id,
            "chunk_id": _string(metadata.get("chunk_id") or metadata.get("id")),
            "chunk_index": _number(_first_present(metadata, "chunk_index", "chunk")),
            "chunk_source": chunk_source,
        },
        "source": {
            "resource_hash": resource_hash,
            "display_name": display_name,
            "source_type": _string(metadata.get("source_type")),
            "file_kind": file_kind,
            "suffix": suffix,
            "mime_type": mimetypes.guess_type(display_name)[0],
            "source_url": _string(metadata.get("url") or metadata.get("source_url")),
        },
        "preview": preview,
        "actions": {
            "download_url": f"/api/evidence/{item_id}/download" if resource_hash else None,
            "source_url": _string(metadata.get("url") or metadata.get("source_url")),
        },
    }


def documents_with_scores(docs: Sequence[tuple[Document, Optional[float]]]) -> List[Document]:
    """Copy retrieved documents and attach scores in metadata for run memory."""
    copied: List[Document] = []
    for doc, score in docs:
        metadata = dict(doc.metadata or {})
        if score is not None:
            metadata["retriever_score"] = score
        copied.append(Document(page_content=doc.page_content, metadata=metadata))
    return copied


def find_evidence_item(payload: Dict[str, Any], item_id: str) -> Optional[Dict[str, Any]]:
    for item in payload.get("items") or []:
        if isinstance(item, dict) and item.get("id") == item_id:
            return item
    for group in payload.get("groups") or []:
        for item in group.get("items") or []:
            if isinstance(item, dict) and item.get("id") == item_id:
                return item
    return None


def _page_metadata(metadata: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    raw = metadata.get("page_number")
    source_key = "page_number"
    if raw is None:
        raw = metadata.get("page")
        source_key = "page"
    if raw is None:
        raw = metadata.get("page_index")
        source_key = "page_index"
    page_num = _int(raw)
    if page_num is None:
        return None
    if source_key in {"page", "page_index"} and page_num >= 0:
        display_page = page_num + 1
        page_index = page_num
    else:
        display_page = page_num
        page_index = max(page_num - 1, 0)
    return {"page_number": display_page, "page_index": page_index, "source_key": source_key}


def _evidence_kind(file_kind: str, chunk_source: Optional[str], page: Optional[Dict[str, Any]]) -> str:
    if file_kind == "image":
        return "image"
    if file_kind == "pdf" and page:
        if chunk_source == "vision_caption":
            return "pdf_page_caption"
        return "pdf_page_text"
    if file_kind in {"text", "pdf"}:
        return "text"
    return "unsupported"


def _preview_info(kind: str, page: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if kind == "image":
        preview_type = "image"
    elif kind in {"pdf_page_caption", "pdf_page_text"} and page:
        preview_type = "pdf_page"
    elif kind == "text":
        preview_type = "text"
    else:
        preview_type = "unsupported"
    return {
        "type": preview_type,
        "available": preview_type != "unsupported",
        "preview_url": None,
        "preview_unavailable": preview_type == "unsupported",
    }


def _file_kind(suffix: str, metadata: Dict[str, Any]) -> str:
    source_type = _string(metadata.get("source_type")) or ""
    if suffix in IMAGE_EXTENSIONS or source_type == "image":
        return "image"
    if suffix == ".pdf":
        return "pdf"
    if suffix in TEXT_EXTENSIONS:
        return "text"
    return "unsupported"


def _suffix(metadata: Dict[str, Any], display_name: str, source_path: Optional[str]) -> str:
    raw = _string(metadata.get("suffix"))
    if raw:
        raw = raw if raw.startswith(".") else f".{raw}"
        return raw.lower()
    for value in (display_name, source_path):
        if value:
            suffix = Path(value).suffix.lower()
            if suffix:
                return suffix
    return ""


def _unit_id(
    metadata: Dict[str, Any],
    rank: Optional[int],
    page: Optional[Dict[str, Any]],
    excerpt: str,
    kind: str,
) -> str:
    if kind == "image":
        return "image"
    if kind in {"pdf_page_caption", "pdf_page_text"} and page:
        return f"page:{page['page_number']}"
    for key in ("chunk_id", "id", "document_chunk_id"):
        value = _string(metadata.get(key))
        if value:
            return value
    chunk_index = _string(_first_present(metadata, "chunk_index", "chunk"))
    if chunk_index:
        return f"chunk:{chunk_index}"
    if page:
        return f"page:{page['page_number']}:{metadata.get('chunk_source', 'text')}"
    return f"rank:{rank or 0}:{_stable_id(excerpt[:120])}"


def _stable_id(*parts: Any) -> str:
    raw = "|".join("" if part is None else str(part) for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def _fallback_hash(metadata: Dict[str, Any], content: str) -> str:
    return _stable_id(metadata.get("source"), metadata.get("path"), content[:120])


def _excerpt(text: str) -> str:
    cleaned = " ".join((text or "").replace("\x00", "").split())
    if len(cleaned) > MAX_EXCERPT_CHARS:
        return f"{cleaned[:MAX_EXCERPT_CHARS].rstrip()}..."
    return cleaned


def _string(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _first_present(metadata: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = metadata.get(key)
        if value is not None and value != "":
            return value
    return None


def _number(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
