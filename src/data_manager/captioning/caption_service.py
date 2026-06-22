"""VLM-based image captioning service for ingest-time caption-then-embed."""

from __future__ import annotations

import base64
import time
from typing import Optional

from src.utils.logging import get_logger

logger = get_logger(__name__)

DEFAULT_CAPTION_PROMPT = (
    "You are an expert scientific figure analyst. Describe this image in detail, covering the following where applicable:\n"
    "- Title: state the exact title of the figure or plot if one is visible.\n"
    "- Plot type: identify the kind of visualization (e.g. line plot, scatter plot, bar chart, heatmap, histogram, image, diagram, table).\n"
    "- Axes: state the exact axis labels and units for each axis. If tick values are visible, note the range.\n"
    "- Legend: if a legend is present, list every entry and what it represents.\n"
    "- Colorbar or heatmap scale: if present, describe the quantity shown, the range, and the units.\n"
    "- Key values and trends: report notable numerical values, peaks, minima, thresholds, or patterns visible in the data.\n"
    "- Conclusions: describe any qualitative or quantitative conclusions that can be drawn from the figure, "
    "given the context provided by surrounding text. Note any anomalies, comparisons, or takeaways a reader would extract.\n"
    "Be precise. Prefer exact values over vague descriptions where they are legible."
)


class CaptioningError(Exception):
    """Raised when image captioning fails permanently."""


class ConfigurationError(Exception):
    """Raised when captioning is misconfigured (e.g. model lacks vision support)."""


class CaptionService:
    """Generates textual captions for images using a vision-language model."""

    def __init__(
        self,
        provider,
        model_name: str,
        prompt: Optional[str] = None,
        max_retries: int = 3,
    ) -> None:
        self.provider = provider
        self.model_name = model_name
        self.prompt = prompt or DEFAULT_CAPTION_PROMPT
        self.max_retries = max_retries

        model_info = provider.get_model_info(model_name)
        if model_info is None:
            raise ConfigurationError(
                f"Model '{model_name}' not found in provider "
                f"'{provider.display_name}'. Available models: "
                f"{[m.id for m in provider.list_models()]}"
            )
        if not model_info.supports_vision:
            raise ConfigurationError(
                f"Model '{model_name}' does not support vision. "
                f"Captioning requires a vision-capable model "
                f"(supports_vision=True). Choose a different model."
            )

        self._chat_model = provider.get_chat_model(model_name)
        logger.info(
            "CaptionService initialized: provider=%s, model=%s",
            provider.display_name,
            model_name,
        )

    def caption_image(
        self,
        image_bytes: bytes,
        mime_type: str,
        context: str = "",
    ) -> str:
        """Generate a textual caption for a single image.

        Args:
            image_bytes: Raw image bytes.
            mime_type: MIME type (e.g. "image/png").
            context: Optional surrounding text for additional context.

        Returns:
            A textual description of the image content.

        Raises:
            CaptioningError: If the VLM call fails after retries.
        """
        b64_image = base64.b64encode(image_bytes).decode("utf-8")
        data_url = f"data:{mime_type};base64,{b64_image}"

        user_content = []
        text_part = self.prompt
        if context:
            text_part += f"\n\nSurrounding text from the document:\n{context}"
        user_content.append({"type": "text", "text": text_part})
        user_content.append(
            {
                "type": "image_url",
                "image_url": {"url": data_url},
            }
        )

        from langchain_core.messages import HumanMessage

        message = HumanMessage(content=user_content)

        last_exc: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            try:
                response = self._chat_model.invoke([message])
                caption = response.content
                if isinstance(caption, str):
                    return caption.strip()
                return str(caption).strip()
            except Exception as exc:
                last_exc = exc
                if attempt < self.max_retries:
                    wait = 2 ** (attempt - 1)
                    logger.warning(
                        "Caption attempt %d/%d failed: %s. Retrying in %ds...",
                        attempt,
                        self.max_retries,
                        exc,
                        wait,
                    )
                    time.sleep(wait)

        raise CaptioningError(
            f"Failed to caption image after {self.max_retries} attempts: {last_exc}"
        )
