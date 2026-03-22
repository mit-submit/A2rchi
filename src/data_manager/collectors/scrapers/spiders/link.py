from typing import Iterator
from urllib.parse import urlparse

from scrapy import Request, Spider
from scrapy.http import Response, TextResponse

from src.data_manager.collectors.scrapers.items import PDFItem, WebPageItem
from src.data_manager.collectors.scrapers.utils import get_content_type, same_host_links


class LinkSpider(Spider):
    """
    Generic link-following spider for unauthenticated pages.
    Stays within the same hostname as start_url, up to max_depth.
    """

    name = "link"
    custom_settings = {
        "DEPTH_LIMIT": 2,  # safety cap; narrowed per-crawl via meta["depth"] check
    }

    def __init__(self, start_url: str = "", max_depth: int = 1, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._start_url = start_url
        self._base_host = urlparse(start_url).netloc
        self._max_depth = int(max_depth)

    async def start(self):
        """
        Seed request — validates start_url at crawl time, not import time.
        Building the habit: always attach errback here, never rely on
        start_urls shortcut in production spiders.
        """
        if not self._start_url:
            raise ValueError("links spider requires -a start_url=<url>")
        yield Request(
            url=self._start_url,
            callback=self.parse,
            errback=self.errback,
            meta={"depth": 0},
        )

    def parse(self, response: Response) -> Iterator[WebPageItem | PDFItem | Request]:
        """
        Extract one item per response, then yield follow Requests up to max_depth.
        @url https://quotes.toscrape.com/
        @returns items 1
        @scrapes url content suffix source_type title
        """
        self.logger.info("Status %s for %s", response.status, response.url)

        yield from self._extract_item(response)

        current_depth = response.meta.get("depth", 0)
        if current_depth >= self._max_depth:
            return

        shlinks = same_host_links(self._base_host, response)
        self.logger.info(
            "Found %d same-host links at depth %d", len(shlinks), current_depth
        )

        for url in shlinks:
            yield Request(
                url=url,
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
    # Private helpers — pure, unit-testable without a reactor
    # ------------------------------------------------------------------ #

    def _extract_item(self, response: Response) -> Iterator[WebPageItem | PDFItem]:
        ct = get_content_type(response)

        if response.url.lower().endswith(".pdf") or "application/pdf" in ct:
            yield PDFItem(
                url=response.url,
                content=response.body,
                suffix="pdf",
                source_type="web",
                title="",
                content_type=ct,
            )
            return

        title = response.css("title::text").get(default="").strip()
        encoding = response.encoding if isinstance(response, TextResponse) else "utf-8"

        yield WebPageItem(
            url=response.url,
            content=response.text,
            suffix="html",
            source_type="web",
            title=title,
            content_type=ct,
            encoding=encoding,
        )

