from typing import Iterator
from urllib.parse import urlparse
from scrapy import Request, Spider
from scrapy.http import Response
from scrapy.linkextractors import LinkExtractor
from scrapy.link import Link
from src.data_manager.collectors.scrapers.items import WebPageItem

class LinkSpider(Spider):
    """
    Generic link-following spider for unauthenticated pages.
    Stays within the same hostname as start_url, up to max_depth.
    """

    name = "link"

    @classmethod
    def from_crawler(cls, crawler, *args, **kwargs):
        max_depth = int(kwargs.get("max_depth", 1))
        max_page = int(kwargs.get("max_page", 0))
        crawler.settings.set("DEPTH_LIMIT", max_depth, priority="spider")
        if max_page:
            crawler.settings.set("CLOSESPIDER_PAGECOUNT", max_page, priority="spider")
        return super().from_crawler(crawler, *args, **kwargs)

    def __init__(self, start_urls: list[str] = None, max_depth: int = 1, max_page: int = 0, allow: list[str] = None, deny: list[str] = None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if start_urls is None:
            raise ValueError("LinkSpider requires start_urls list parameter")
        self._start_urls = start_urls
        self._base_host = urlparse(start_urls[0]).netloc
        self._max_depth = int(max_depth)
        self._max_page = int(max_page)
        self._le = LinkExtractor(
            allow=allow or [],
            deny=deny or [],
            allow_domains=[self._base_host],
            deny_extensions=[".jpg", ".jpeg", ".png", ".gif",
                            ".bmp", ".svg", ".ico", ".webp"],
            unique=True,
        )

    async def start(self):
        """
        Seed requests — validates start_urls at crawl time, not import time.
        Building the habit: always attach errback here, never rely on
        start_urls shortcut in production spiders.
        """
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
        if current_depth >= self._max_depth:
            return
        for link in self.parse_follow_links(response):
            self.logger.info("Following %s at depth %d", link.url, current_depth)
            yield Request(
                link.url,
                callback=self.parse,
                errback=self.errback,
                meta={"depth": current_depth + 1},
            )
    
    def errback(self, failure):
        self.logger.error(
            "Request failed: %s — %s",
            failure.request.url,
            repr(failure.value),
        )

    # ------------------------------------------------------------------ #
    # Extension points — pure, unit-testable without a reactor
    # ------------------------------------------------------------------ #

    def parse_item(self, response: Response) -> Iterator[WebPageItem]:
        raise NotImplementedError("parse_item must be implemented by the subclass")

    def parse_follow_links(self, response: Response) -> Iterator[Link]:
        yield from self._le.extract_links(response)