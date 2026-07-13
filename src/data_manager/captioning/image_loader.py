"""Image loading utilities for caption-then-embed ingestion."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from src.utils.logging import get_logger

logger = get_logger(__name__)

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff", ".tif", ".webp"}

MIME_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".tiff": "image/tiff",
    ".tif": "image/tiff",
    ".webp": "image/webp",
}


@dataclass
class ImageDocument:
    """A single image extracted from a file or PDF page."""

    image_bytes: bytes
    mime_type: str
    metadata: Dict[str, str] = field(default_factory=dict)
    surrounding_text: str = ""


def load_images_from_file(file_path: Path | str) -> List[ImageDocument]:
    """Load a standalone image file and return it as an ImageDocument.

    Args:
        file_path: Path to the image file.

    Returns:
        A list with a single ImageDocument, or empty if unsupported format.

    Raises:
        FileNotFoundError: If the file does not exist.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Image file not found: {path}")

    ext = path.suffix.lower()
    if ext not in IMAGE_EXTENSIONS:
        logger.warning("Unsupported image format: %s", ext)
        return []

    mime_type = MIME_TYPES.get(ext, "application/octet-stream")
    image_bytes = path.read_bytes()

    return [
        ImageDocument(
            image_bytes=image_bytes,
            mime_type=mime_type,
            metadata={
                "source_file": path.name,
                "page_number": "1",
            },
        )
    ]


def load_images_from_pdf(
    file_path: Path | str,
    caption_mode: str = "gated",
    min_drawings: int = 5,
    render_dpi: int = 150,
) -> List[ImageDocument]:
    """Render PDF pages to images for captioning.

    Args:
        file_path: Path to the PDF file.
        caption_mode: "all" (every page), "gated" (visual-content pages only),
                      or "none" (disabled).
        min_drawings: Vector-drawing count threshold for gated mode. Pages with
                      at least this many drawings (or any embedded raster images)
                      are considered visual and will be captioned.
        render_dpi: DPI for page rendering.

    Returns:
        List of ImageDocument instances for qualifying pages.
    """
    if caption_mode == "none":
        return []

    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"PDF file not found: {path}")

    import fitz  # PyMuPDF

    results: List[ImageDocument] = []
    doc = fitz.open(str(path))
    try:
        for page_num in range(len(doc)):
            page = doc.load_page(page_num)
            has_embedded_images = bool(page.get_images(full=True))
            drawing_count = len(page.get_drawings())

            if caption_mode == "gated" and not has_embedded_images and drawing_count < min_drawings:
                logger.debug(
                    "Skipping page %d of %s: no embedded images and only %d drawings (threshold %d)",
                    page_num + 1,
                    path.name,
                    drawing_count,
                    min_drawings,
                )
                continue

            page_text = page.get_text("text") or ""
            zoom = render_dpi / 72.0
            mat = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=mat)
            image_bytes = pix.tobytes("png")

            results.append(
                ImageDocument(
                    image_bytes=image_bytes,
                    mime_type="image/png",
                    metadata={
                        "source_file": path.name,
                        "page_number": str(page_num + 1),
                    },
                    surrounding_text=page_text.strip(),
                )
            )
    finally:
        doc.close()

    logger.info(
        "Extracted %d page images from %s (mode=%s, min_drawings=%d)",
        len(results),
        path.name,
        caption_mode,
        min_drawings,
    )
    return results
