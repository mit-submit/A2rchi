"""
Single-dispatch adapter: converts Scrapy Items into ScrapedResource.
 
Design principles:
- Items are dumb data bags. They know nothing about ScrapedResource.
- This is the ONLY place that knows about both schemas.
- New sources: add a @to_scraped_resource.register block here. Touch nothing else.
- Do NOT reconstruct ResourceMetadata — ScrapedResource.get_metadata() already
  derives display_name, url, suffix, source_type from raw fields. Pass raw values only.
 
Constraint: ~50 LOC of logic.
 
Adding a new source (e.g. TwikiPageItem):
    @to_scraped_resource.register(TwikiPageItem)
    def _twiki(item) -> ScrapedResource:
        ...
 
If two sources share identical mapping logic, stack decorators:
    @to_scraped_resource.register(WebPageItem)
    @to_scraped_resource.register(TwikiPageItem)
    def _html_page(item) -> ScrapedResource:
        ...
    Note: do NOT use union type hints (WebPageItem | TwikiPageItem) —
    singledispatch ignores annotations, it dispatches on runtime type only.
"""
from __future__ import annotations
 
from functools import singledispatch
 
from src.data_manager.collectors.scrapers.scraped_resource import ScrapedResource
from src.data_manager.collectors.scrapers.items import WebPageItem, TWikiPageItem
 
 
@singledispatch
def to_scraped_resource(item) -> ScrapedResource:
    """Raises for unregistered types — fail loudly, never silently skip."""
    raise TypeError(
        f"No adapter registered for item type {type(item).__name__!r}. "
        "Add @to_scraped_resource.register(YourItemClass) in this module."
    )
 
@to_scraped_resource.register(WebPageItem)
def _web(item): return _html_page(item, source_type="web")

@to_scraped_resource.register(TWikiPageItem)
def _twiki(item): return _html_page(item, source_type="twiki")

def _html_page(item, source_type) -> ScrapedResource:
    return ScrapedResource(
        url=item["url"],
        content=item["content"],
        suffix=item.get("suffix", "html"),
        source_type=source_type,
        metadata={
            "content_type": item.get("content_type"),
            "encoding": item.get("encoding"),
            "title": item.get("title"),
        },
    )
