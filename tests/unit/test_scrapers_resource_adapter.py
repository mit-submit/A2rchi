import pytest

from src.data_manager.collectors.scrapers.resource_adapter import to_scraped_resource
from src.data_manager.collectors.scrapers.items import WebPageItem, TWikiPageItem
from src.data_manager.collectors.scrapers.scraped_resource import ScrapedResource


# ---------------------------------------------------------------------------
# WebPageItem adapter
# ---------------------------------------------------------------------------

# class TestWebAdapter:
#     def _make_item(self, **overrides) -> WebPageItem:
#         base = {
#             "url": "https://example.com/page",
#             "content": "<html><body>hello</body></html>",
#             "suffix": "html",
#             "content_type": "text/html; charset=utf-8",
#             "encoding": "utf-8",
#             "title": "Example Page",
#         }
#         return WebPageItem({**base, **overrides})
# 
#     def test_returns_scraped_resource(self):
#         result = to_scraped_resource(self._make_item())
#         assert isinstance(result, ScrapedResource)
# 
#     def test_url_passthrough(self):
#         item = self._make_item(url="https://example.com/foo")
#         assert to_scraped_resource(item).url == "https://example.com/foo"
# 
#     def test_content_passthrough(self):
#         item = self._make_item(content="<p>hi</p>")
#         assert to_scraped_resource(item).content == "<p>hi</p>"
# 
#     def test_suffix(self):
#         assert to_scraped_resource(self._make_item(suffix="pdf")).suffix == "pdf"
# 
#     def test_source_type_is_web(self):
#         assert to_scraped_resource(self._make_item()).source_type == "web"
# 
#     def test_metadata_content_type(self):
#         item = self._make_item(content_type="text/html")
#         assert to_scraped_resource(item).metadata["content_type"] == "text/html"
# 
#     def test_metadata_encoding(self):
#         item = self._make_item(encoding="utf-8")
#         assert to_scraped_resource(item).metadata["encoding"] == "utf-8"
# 
#     def test_metadata_title(self):
#         item = self._make_item(title="My Title")
#         assert to_scraped_resource(item).metadata["title"] == "My Title"
# 
#     def test_optional_fields_absent(self):
#         """Adapter must not crash when optional fields are missing."""
#         item = WebPageItem({
#             "url": "https://example.com/page",
#             "content": "body",
#             "suffix": "html",
#         })
#         result = to_scraped_resource(item)
#         assert result.metadata.get("title") is None
#         assert result.metadata.get("encoding") is None
# 
#     def test_binary_content_passthrough(self):
#         """PDF bytes must pass through unchanged."""
#         raw = b"%PDF-1.4 binary content"
#         item = self._make_item(content=raw, suffix="pdf", content_type="application/pdf")
#         result = to_scraped_resource(item)
#         assert result.content == raw
#         assert result.is_binary


# ---------------------------------------------------------------------------
# TWikiPageItem adapter
# ---------------------------------------------------------------------------

class TestTWikiPageItemAdapter:
    def _make_item(self, **overrides) -> TWikiPageItem:
        base = {
            "url": "https://twiki.cern.ch/twiki/bin/view/CMSPublic/CRAB3ConfigurationFile",
            "content": "<html>CRAB3ConfigurationFile</html>",
            "title": "CRAB3ConfigurationFile",
        }
        return TWikiPageItem({**base, **overrides})

    def test_returns_scraped_resource(self):
        assert isinstance(to_scraped_resource(self._make_item()), ScrapedResource)

    def test_default_source_type_is_twiki(self):
        assert to_scraped_resource(self._make_item()).source_type == "twiki"

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
