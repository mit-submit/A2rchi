"""
Scrapy intuition — Items as the data contract (FR-7a):

    Items sit between Parser and Adapter.
    Their field schema must be driven by what the Adapter needs
    to construct a ScrapedResource — not by what's convenient
    to inspect during development.

    Wrong mental model: "what fields help me debug?"
    Right mental model: "what fields does ScrapedResource.__init__ need?"

    ScrapedResource fields (from scraped_resource.py):
        url          — required
        content      — required (str or bytes)
        suffix       — required
        source_type  — required ("web", "sso", "git")
        metadata     — dict, optional (title, content_type, encoding, etc.)
        file_name    — optional
        relative_path — optional

    So items carry exactly those fields.
    Debug fields (body_preview, body_length) belong in logger calls,
    not in the item schema — otherwise the adapter becomes a translation
    layer for data that should never have been structured in the first place.

SOLID note — Open/Closed:
    Add new Item subclasses for new source types.
    Do not add source-specific fields to the base class.
    The adapter is the extension point, not the Item.
"""

import scrapy


class BasePageItem(scrapy.Item):
    """
    Common fields shared across all scraped source types.
    Maps directly to ScrapedResource constructor arguments.
    """
    url = scrapy.Field()
    content = scrapy.Field()       # Full text or bytes — NOT a preview
    suffix = scrapy.Field()        # "html", "pdf", "md" etc.
    source_type = scrapy.Field()   # "web" | "twiki" | "indico" | "discourse"

    # Metadata fields — become ScrapedResource.metadata dict
    title = scrapy.Field()
    content_type = scrapy.Field()  # HTTP Content-Type header value
    encoding = scrapy.Field()      # HTTP response encoding

    # Optional — used by git/SSO scrapers for filesystem layout
    file_name = scrapy.Field()
    relative_path = scrapy.Field()


class WebPageItem(BasePageItem):
    """
    Generic page item, works for SSO-*, ordinary web page.
    No extra fields needed beyond BasePageItem.
    Subclassing is the extension point (OCP) — Twiki quirks
    belong in parse_twiki_page(), not in a bloated base class.
    """
    pass


class IndicoPageItem(BasePageItem):
    """
    Indico-specific item.
    Indico API responses carry an event_id and category — useful
    for metadata routing in the adapter without polluting the base.
    """
    event_id = scrapy.Field()
    category = scrapy.Field()

