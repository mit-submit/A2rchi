import pytest

from src.data_manager.collectors.scrapers.resource_adapter import to_scraped_resource
from src.data_manager.collectors.scrapers.items import TWikiPageItem, PDFItem
from src.data_manager.collectors.scrapers.scraped_resource import ScrapedResource

# ---------------------------------------------------------------------------
# WebPageItem, TWikiPageItem adapter
# ---------------------------------------------------------------------------

class TestWebPageItemAdapter:
    def _make_item(self, **overrides) -> TWikiPageItem:
        base = {
            "url": "https://twiki.cern.ch/twiki/bin/view/CMSPublic/CRAB3ConfigurationFile",
            "content": "<html>CRAB3ConfigurationFile</html>",
            "title": "CRAB3ConfigurationFile",
        }
        return TWikiPageItem({**base, **overrides})

    def test_returns_scraped_resource(self):
        assert isinstance(to_scraped_resource(self._make_item()), ScrapedResource)

    def test_default_source_type_is_web(self):
        assert to_scraped_resource(self._make_item()).source_type == "web"

# ---------------------------------------------------------------------------
# PDFItem adapter
# ---------------------------------------------------------------------------

class TestPDFAdapter:
    def _make_item(self, **overrides) -> PDFItem:
        base = {
            "url": "https://mit-teal.github.io/801/textbook/2ed_chapter01.pdf",
            "content": b"%PDF-1.4\n%mock pdf content\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n",
            "title": "mock pdf",
            "suffix": "pdf",
            "content_type": "application/pdf",
        }
        return PDFItem({**base, **overrides})

    def test_returns_scraped_resource(self):
        assert isinstance(to_scraped_resource(self._make_item()), ScrapedResource)

    def test_default_source_type_is_web(self):
        assert to_scraped_resource(self._make_item()).source_type == "web"

# ---------------------------------------------------------------------------
# Unregistered item type — must fail loudly
# ---------------------------------------------------------------------------

class TestUnregisteredItem:
    def test_raises_type_error_for_unknown_item(self):
        """Adapter must raise, never silently return None or a half-baked resource."""

        class UnknownItem(dict):
            pass

        with pytest.raises(TypeError, match="No adapter registered"):
            to_scraped_resource(UnknownItem({"url": "x", "content": "y"}))
