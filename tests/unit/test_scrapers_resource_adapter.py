import pytest

from src.data_manager.collectors.scrapers.adapters import to_scraped_resource
from src.data_manager.collectors.scrapers.items import WebPageItem
from src.data_manager.collectors.scrapers.scraped_resource import ScrapedResource

# ---------------------------------------------------------------------------
# WebPageItem adapter
# ---------------------------------------------------------------------------

class TestWebPageItemAdapter:
    def _make_item(self, **overrides) -> WebPageItem:
        base = {
            "url": "https://twiki.cern.ch/twiki/bin/view/CMSPublic/CRAB3ConfigurationFile",
            "content": "<html>CRAB3ConfigurationFile</html>",
            "title": "CRAB3ConfigurationFile",
            "suffix": "html",
            "source_type": "web",
            "content_type": "text/html",
            "encoding": "utf-8",
        }
        return WebPageItem({**base, **overrides})

    def test_returns_scraped_resource(self):
        assert isinstance(to_scraped_resource(self._make_item()), ScrapedResource)

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
