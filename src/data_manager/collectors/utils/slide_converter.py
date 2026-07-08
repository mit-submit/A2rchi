"""Utility for converting presentation files to markdown using MarkItDown."""

import io
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Union

from markitdown import MarkItDown

from src.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class ConversionResult:
    """Result of a slide-to-markdown conversion."""

    markdown: str
    source_path: Optional[Path] = None
    source_filename: Optional[str] = None
    error: Optional[str] = None


class SlideConverter:
    """Convert presentation files to markdown using MarkItDown.

    Supports PDF, PPTX, PPT, ODP, and other formats supported by MarkItDown.
    Includes extensibility hooks for future plot/figure processing.
    """

    def __init__(self, llm_client=None, llm_model=None):
        """Initialize the slide converter.

        Args:
            llm_client: Optional LLM client for image descriptions (future use)
            llm_model: Optional LLM model name for image descriptions (future use)
        """
        self.md = MarkItDown(
            enable_plugins=False,
            llm_client=llm_client,
            llm_model=llm_model,
        )
        self._plot_hooks: List[Callable] = []
        logger.info("SlideConverter initialized")

    def convert(
        self, file_path: Union[str, Path], content_type: Optional[str] = None
    ) -> ConversionResult:
        """Convert a slide file to markdown.

        Args:
            file_path: Path to the file to convert
            content_type: Optional MIME type hint

        Returns:
            ConversionResult with markdown content
        """
        try:
            file_path = Path(file_path)
            if not file_path.exists():
                return ConversionResult(
                    markdown="",
                    source_path=file_path,
                    error=f"File not found: {file_path}",
                )

            logger.info(f"Converting file to markdown: {file_path.name}")
            result = self.md.convert(str(file_path))

            markdown = (
                result.text_content if hasattr(result, "text_content") else str(result)
            )
            markdown = self._clean_markdown(markdown)

            logger.info(
                f"Converted {file_path.name} to markdown ({len(markdown)} chars)"
            )

            return ConversionResult(
                markdown=markdown,
                source_path=file_path,
                source_filename=file_path.name,
            )

        except Exception as e:
            logger.error(f"Error converting {file_path}: {e}")
            return ConversionResult(
                markdown="",
                source_path=file_path,
                source_filename=file_path.name if isinstance(file_path, Path) else None,
                error=str(e),
            )

    def convert_bytes(
        self, file_bytes: bytes, content_type: str, filename: Optional[str] = None
    ) -> ConversionResult:
        """Convert file bytes to markdown.

        This is useful for converting downloaded attachments without saving to disk.

        Args:
            file_bytes: File content as bytes
            content_type: MIME type (e.g., 'application/pdf')
            filename: Optional filename hint for extension detection

        Returns:
            ConversionResult with markdown content
        """
        try:
            # Determine file extension from content_type or filename
            extension = self._get_extension_from_content_type(content_type, filename)

            # Create a temporary file to feed to MarkItDown
            # (MarkItDown's convert() method expects a file path)
            with tempfile.NamedTemporaryFile(
                suffix=extension, delete=False
            ) as tmp_file:
                tmp_file.write(file_bytes)
                tmp_path = Path(tmp_file.name)

            try:
                logger.info(
                    f"Converting bytes to markdown (type: {content_type}, size: {len(file_bytes)} bytes)"
                )
                result = self.md.convert(str(tmp_path))

                markdown = (
                    result.text_content
                    if hasattr(result, "text_content")
                    else str(result)
                )
                markdown = self._clean_markdown(markdown)

                logger.info(f"Converted to markdown ({len(markdown)} chars)")

                return ConversionResult(
                    markdown=markdown,
                    source_filename=filename,
                )
            finally:
                # Clean up temporary file
                tmp_path.unlink(missing_ok=True)

        except Exception as e:
            logger.error(f"Error converting bytes (type: {content_type}): {e}")
            return ConversionResult(markdown="", source_filename=filename, error=str(e))

    # Regex matching <latexit ...>...</latexit> blocks that MarkItDown extracts
    # from PDFs with embedded LaTeX.  These base64-encoded blobs add no
    # retrieval value and can inflate chunk counts by 5-10x on formula-heavy
    # slides (e.g. physics keynotes).
    _LATEXIT_RE = re.compile(
        r"<latexit\b[^>]*>.*?</latexit>",
        re.DOTALL,
    )

    @classmethod
    def _clean_markdown(cls, markdown: str) -> str:
        """Post-process converted markdown to remove noise."""
        # Strip <latexit> blocks
        cleaned = cls._LATEXIT_RE.sub("", markdown)
        # Collapse runs of blank lines left behind by the removal
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return cleaned.strip()

    def register_plot_hook(self, hook: Callable) -> None:
        """Register a hook for future plot processing.

        This is a placeholder for future extensibility. Plot hooks can be
        called after conversion to extract and analyze plots/figures.

        Args:
            hook: Callable that processes plot data
        """
        self._plot_hooks.append(hook)
        logger.info(
            f"Registered plot hook: {hook.__name__ if hasattr(hook, '__name__') else 'unnamed'}"
        )

    def _get_extension_from_content_type(
        self, content_type: str, filename: Optional[str] = None
    ) -> str:
        """Determine file extension from content type or filename.

        Args:
            content_type: MIME type
            filename: Optional filename

        Returns:
            File extension with leading dot (e.g., '.pdf')
        """
        # Try to get extension from filename first
        if filename:
            ext = Path(filename).suffix
            if ext:
                return ext

        # Map common content types to extensions
        content_type_map = {
            "application/pdf": ".pdf",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
            "application/vnd.ms-powerpoint": ".ppt",
            "application/vnd.oasis.opendocument.presentation": ".odp",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
            "application/msword": ".doc",
        }

        return content_type_map.get(content_type, ".bin")

    def _extract_image_refs(self, result) -> List[str]:
        """Extract image references from conversion result.

        This is a placeholder for future plot extraction functionality.

        Args:
            result: MarkItDown conversion result

        Returns:
            List of image references/paths
        """
        # Future: Parse markdown for image references
        # Future: Call registered plot hooks
        return []
