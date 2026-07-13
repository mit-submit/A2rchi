"""Captioning package for multimodal ingestion via caption-then-embed."""

from src.data_manager.captioning.caption_service import (
    CaptioningError,
    CaptionService,
    ConfigurationError,
)

__all__ = ["CaptionService", "CaptioningError", "ConfigurationError"]
