from typing import Iterator
from urllib.parse import urlparse
from scrapy import Request, Spider
from scrapy.http import Response
from scrapy.linkextractors import LinkExtractor
from scrapy.link import Link
from src.data_manager.collectors.scrapers.utils import _IMAGE_EXTS
from src.data_manager.collectors.scrapers.items import WebPageItem

class LinkSpider(Spider):
    """
    Generic link-following spider for unauthenticated pages.
    Stays within the same hostname as start_url, up to max_depth.
    """

    name = "link"

    custom_settings = {
        "DEPTH_LIMIT": 1, # Default max depth
        "DOWNLOAD_DELAY": 2, # Default (download) delay
        "CLOSESPIDER_PAGECOUNT": 500 # Default max pages
    }

    @classmethod
    def from_crawler(cls, crawler, *args, **kwargs):
        max_depth = kwargs.get("max_depth")
        max_pages = kwargs.get("max_pages")
        delay = kwargs.get("delay")
        if max_depth:
            crawler.settings.set("DEPTH_LIMIT", max_depth, priority="spider")
        if max_pages:
            crawler.settings.set("CLOSESPIDER_PAGECOUNT", max_pages, priority="spider")
        if delay:
            crawler.settings.set("DOWNLOAD_DELAY", delay, priority="spider")
        return super().from_crawler(crawler, *args, **kwargs)

    def __init__(self, start_urls: list[str] = None, max_depth: int = None, max_pages: int = None, allow: list[str] = None, deny: list[str] = None, delay: int = None, canonicalize: bool = False, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._start_urls = start_urls or getattr(self, "_DEFAULT_START_URLS", [])
        self._base_host = urlparse(start_urls[0]).netloc if start_urls else None
        self._le = LinkExtractor(
            allow=allow or [],
            deny=deny or [],
            allow_domains=[self._base_host] if self._base_host else [],
            deny_extensions=list(_IMAGE_EXTS),
            canonicalize=canonicalize,
            unique=True,
        )

    async def start(self):
        """
        Seed requests — validates start_urls at crawl time, not import time.
        Building the habit: always attach errback here, never rely on
        start_urls shortcut in production spiders.
        """
        if not self._start_urls:
            raise ValueError("LinkSpider requires start_urls to be set")
        for url in self._start_urls:
            yield Request(url=url, callback=self.parse, errback=self.errback, meta={"depth": 0})

    def parse(self, response: Response) -> Iterator[WebPageItem | Request]:
        """
        Extract one item per response, then yield follow Requests up to max_depth.
        """
        yield from self.parse_item(response) # Yield Items
        yield from self.follow_links(response) # Yield Requests

    
    def follow_links(self, response: Response) -> Iterator[Request]:
        current_depth = response.meta.get("depth", 0)
        if current_depth >= self.settings.get("DEPTH_LIMIT"):
            self.logger.info("Reached max depth %d", self.settings.get("DEPTH_LIMIT"))
            return
        for link in self.parse_follow_links(response):
            self.logger.info("Following %s at depth %d", link.url, current_depth)
            yield Request(link.url, callback=self.parse, errback=self.errback, meta={"depth": current_depth + 1})
    
    def errback(self, failure):
        self.logger.error(
            "Request failed: %s — %s",
            failure.request.url,
            repr(failure.value),
        )

    # ------------------------------------------------------------------ #
    # Extension points — pure, unit-testable/checkable without a reactor
    # ------------------------------------------------------------------ #

    def parse_item(self, response: Response) -> Iterator[WebPageItem]:
        raise NotImplementedError("parse_item must be implemented by the subclass")

    def parse_follow_links(self, response: Response) -> Iterator[Link]:
        yield from self._le.extract_links(response)