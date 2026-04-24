"""Unit tests for the captioning package (caption-then-embed)."""

import importlib
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image
from jinja2 import ChainableUndefined, Environment, FileSystemLoader

from src.data_manager.captioning.caption_service import (
    CaptioningError,
    CaptionService,
    ConfigurationError,
)
from src.cli.utils.helpers import _render_config_for_compare
from src.data_manager.captioning.image_loader import (
    IMAGE_EXTENSIONS,
    MIME_TYPES,
    ImageDocument,
    load_images_from_file,
    load_images_from_pdf,
)
from src.data_manager.vectorstore.loader_utils import (
    IMAGE_EXTENSIONS as LOADER_IMAGE_EXTENSIONS,
)
from src.data_manager.vectorstore.loader_utils import (
    is_image_file,
)
from src.utils.file_acceptance import get_effective_accepted_files

# ---------------------------------------------------------------------------
# image_loader tests
# ---------------------------------------------------------------------------


class TestImageDocument:
    def test_creation(self):
        doc = ImageDocument(image_bytes=b"\x89PNG", mime_type="image/png")
        assert doc.image_bytes == b"\x89PNG"
        assert doc.mime_type == "image/png"
        assert doc.metadata == {}
        assert doc.surrounding_text == ""

    def test_creation_with_metadata(self):
        meta = {"page_number": "3", "source_file": "test.pdf"}
        doc = ImageDocument(image_bytes=b"data", mime_type="image/jpeg", metadata=meta)
        assert doc.metadata["page_number"] == "3"


class TestLoadImagesFromFile:
    def test_unsupported_extension(self, tmp_path):
        txt = tmp_path / "readme.txt"
        txt.write_text("hello")
        result = load_images_from_file(txt)
        assert result == []

    def test_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            load_images_from_file("/nonexistent/photo.png")

    def test_loads_png(self, tmp_path):
        png = tmp_path / "image.png"
        png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        result = load_images_from_file(png)
        assert len(result) == 1
        doc = result[0]
        assert doc.mime_type == "image/png"
        assert "source_file" in doc.metadata
        assert doc.image_bytes.startswith(b"\x89PNG")

    def test_loads_jpeg(self, tmp_path):
        jpg = tmp_path / "photo.jpg"
        jpg.write_bytes(b"\xff\xd8\xff" + b"\x00" * 50)
        result = load_images_from_file(jpg)
        assert len(result) == 1
        assert result[0].mime_type == "image/jpeg"


class TestLoadImagesFromPdf:
    @staticmethod
    def _create_pdf(tmp_path, *, text, with_image):
        import fitz

        pdf_path = tmp_path / "test.pdf"
        doc = fitz.open()
        page = doc.new_page()
        page.insert_textbox(fitz.Rect(72, 72, 400, 500), text)

        if with_image:
            image_path = tmp_path / "embedded.png"
            Image.new("RGB", (40, 40), color="red").save(image_path)
            page.insert_image(
                fitz.Rect(72, 320, 220, 460),
                filename=str(image_path),
            )

        doc.save(str(pdf_path))
        doc.close()
        return pdf_path

    def test_gated_mode_keeps_page_with_embedded_image(self, tmp_path):
        pdf_path = self._create_pdf(
            tmp_path,
            text="This page has enough extracted text to exceed the threshold. " * 8,
            with_image=True,
        )

        result = load_images_from_pdf(
            pdf_path,
            caption_mode="gated",
            min_text_chars=200,
        )

        assert len(result) == 1
        assert result[0].metadata["page_number"] == "1"
        assert int(result[0].metadata["page_text_chars"]) >= 200

    def test_gated_mode_skips_text_heavy_page_without_embedded_image(self, tmp_path):
        pdf_path = self._create_pdf(
            tmp_path,
            text="This page has enough extracted text to exceed the threshold. " * 8,
            with_image=False,
        )

        result = load_images_from_pdf(
            pdf_path,
            caption_mode="gated",
            min_text_chars=200,
        )

        assert result == []


class TestImageExtensions:
    def test_common_extensions_present(self):
        for ext in [".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff", ".webp"]:
            assert ext in IMAGE_EXTENSIONS

    def test_mime_types_match(self):
        for ext in IMAGE_EXTENSIONS:
            assert ext in MIME_TYPES, f"Missing MIME type for {ext}"


# ---------------------------------------------------------------------------
# is_image_file tests
# ---------------------------------------------------------------------------


class TestIsImageFile:
    def test_image_extensions(self):
        assert is_image_file("photo.png") is True
        assert is_image_file("photo.JPG") is True
        assert is_image_file("/path/to/image.jpeg") is True
        assert is_image_file("diagram.gif") is True
        assert is_image_file("scan.tiff") is True

    def test_non_image_extensions(self):
        assert is_image_file("doc.pdf") is False
        assert is_image_file("readme.txt") is False
        assert is_image_file("code.py") is False
        assert is_image_file("data.json") is False

    def test_loader_extensions_match(self):
        assert LOADER_IMAGE_EXTENSIONS == IMAGE_EXTENSIONS


class TestVectorStoreManagerCaptioningConfig:
    @staticmethod
    def _build_template_env():
        template_dir = Path(__file__).resolve().parents[2] / "src" / "cli" / "templates"
        return Environment(
            loader=FileSystemLoader(str(template_dir)),
            undefined=ChainableUndefined,
        )

    @staticmethod
    def _load_vectorstore_manager():
        sys.modules.setdefault("nltk", MagicMock())
        module = importlib.import_module("src.data_manager.vectorstore.manager")
        return module.VectorStoreManager

    def test_pdf_caption_settings_support_flat_config_keys(self):
        VectorStoreManager = self._load_vectorstore_manager()
        manager = object.__new__(VectorStoreManager)
        manager._captioning_config = {
            "caption_mode": "all",
            "min_text_chars": "42",
            "dpi": "144",
        }

        assert manager._get_pdf_caption_settings() == ("all", 42, 144)

    def test_rendered_config_uses_flat_captioning_keys_only(self):
        config = {
            "name": "captioning-test",
            "data_manager": {
                "captioning": {
                    "enabled": True,
                    "provider": "openai",
                    "model": "gpt-4o",
                    "caption_mode": "all",
                    "min_text_chars": 64,
                    "dpi": 288,
                }
            },
        }

        rendered = _render_config_for_compare(
            config,
            host_mode=False,
            verbosity=0,
            env=self._build_template_env(),
        )

        assert rendered["data_manager"]["captioning"]["caption_mode"] == "all"
        assert rendered["data_manager"]["captioning"]["min_text_chars"] == 64
        assert rendered["data_manager"]["captioning"]["dpi"] == 288
        assert "pdf" not in rendered["data_manager"]["captioning"]
        assert "image" not in rendered["data_manager"]["captioning"]


class TestEffectiveAcceptedFiles:
    def test_captioning_adds_image_extensions_when_accept_list_missing(self):
        accepted = get_effective_accepted_files(
            {
                "global": {},
                "data_manager": {"captioning": {"enabled": True}},
            }
        )

        assert accepted[:2] == [".pdf", ".md"]
        for ext in IMAGE_EXTENSIONS:
            assert ext in accepted

    def test_captioning_disabled_uses_non_image_default_accept_list(self):
        accepted = get_effective_accepted_files(
            {
                "global": {},
                "data_manager": {"captioning": {"enabled": False}},
            }
        )

        assert accepted == [
            ".pdf",
            ".md",
            ".txt",
            ".docx",
            ".html",
            ".htm",
            ".json",
            ".yaml",
            ".yml",
            ".py",
            ".js",
            ".ts",
            ".jsx",
            ".tsx",
            ".java",
            ".go",
            ".rs",
            ".c",
            ".cpp",
            ".h",
            ".sh",
        ]

    def test_explicit_accept_list_trumps_captioning(self):
        accepted = get_effective_accepted_files(
            {
                "global": {"ACCEPTED_FILES": [".pdf", ".md"]},
                "data_manager": {"captioning": {"enabled": True}},
            }
        )

        assert accepted == [".pdf", ".md"]

    def test_explicit_empty_accept_list_is_preserved(self):
        accepted = get_effective_accepted_files(
            {
                "global": {"ACCEPTED_FILES": []},
                "data_manager": {"captioning": {"enabled": True}},
            }
        )

        assert accepted == []


# ---------------------------------------------------------------------------
# CaptionService tests
# ---------------------------------------------------------------------------


def _make_mock_provider(supports_vision: bool = True):
    provider = MagicMock()
    provider.supports_vision = supports_vision
    provider.display_name = "TestProvider"
    provider.provider_type = MagicMock()
    provider.provider_type.value = "test"

    model_info = MagicMock()
    model_info.supports_vision = supports_vision
    model_info.id = "test-model"
    provider.get_model_info.return_value = model_info
    provider.list_models.return_value = [model_info]
    return provider


class TestCaptionServiceInit:
    def test_non_vision_provider_raises(self):
        provider = _make_mock_provider(supports_vision=False)
        with pytest.raises(ConfigurationError, match="vision"):
            CaptionService(provider=provider, model_name="test-model")

    def test_model_not_found_raises(self):
        provider = _make_mock_provider()
        provider.get_model_info.return_value = None
        with pytest.raises(ConfigurationError, match="not found"):
            CaptionService(provider=provider, model_name="nonexistent-model")

    def test_valid_init(self):
        provider = _make_mock_provider()
        svc = CaptionService(provider=provider, model_name="test-model")
        assert svc.model_name == "test-model"

    def test_custom_prompt(self):
        provider = _make_mock_provider()
        svc = CaptionService(
            provider=provider, model_name="test-model", prompt="Describe this image."
        )
        assert svc.prompt == "Describe this image."


class TestCaptionServiceCaption:
    def _make_service(self):
        provider = _make_mock_provider()
        mock_llm = MagicMock()
        provider.get_chat_model.return_value = mock_llm
        svc = CaptionService(provider=provider, model_name="test-model")
        return svc, mock_llm

    def test_caption_image_returns_text(self):
        svc, mock_llm = self._make_service()
        mock_llm.invoke.return_value = MagicMock(
            content="A scatter plot showing energy vs momentum"
        )

        result = svc.caption_image(image_bytes=b"\x89PNG" * 10, mime_type="image/png")
        assert result == "A scatter plot showing energy vs momentum"
        mock_llm.invoke.assert_called_once()

    def test_caption_image_retries_on_failure(self):
        svc, mock_llm = self._make_service()
        mock_llm.invoke.side_effect = [
            Exception("rate limit"),
            MagicMock(content="A histogram"),
        ]

        with patch("src.data_manager.captioning.caption_service.time.sleep"):
            result = svc.caption_image(
                image_bytes=b"\xff\xd8\xff" * 10, mime_type="image/jpeg"
            )
        assert result == "A histogram"
        assert mock_llm.invoke.call_count == 2

    def test_caption_image_all_retries_fail(self):
        svc, mock_llm = self._make_service()
        mock_llm.invoke.side_effect = Exception("persistent error")

        with patch("src.data_manager.captioning.caption_service.time.sleep"):
            with pytest.raises(CaptioningError, match="persistent error"):
                svc.caption_image(image_bytes=b"\x89PNG" * 10, mime_type="image/png")
