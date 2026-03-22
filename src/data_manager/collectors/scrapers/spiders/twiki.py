import logging
from typing import Iterator
from scrapy import Spider, Request
from scrapy.http import Response
from urllib.parse import urlparse
from src.data_manager.collectors.scrapers.items import TWikiPageItem
from src.data_manager.collectors.scrapers.utils import get_content_type, same_host_links

logger = logging.getLogger(__name__)

class TwikiSpider(Spider):
    """
    Minimal Twiki spider against a real Twiki target.
    Public page — no SSO needed — isolates lifecycle learning from auth complexity.
    """

    name = "twiki"

    custom_settings = {
        "ROBOTSTXT_OBEY": False,
        "DOWNLOAD_DELAY": 60,
        "DOWNLOAD_TIMEOUT": 120,
        "RETRY_TIMES": 0,
    }

    async def start(self):
        """
        Seed request for the CRAB3 Twiki config page.
        Building the habit: always use start_requests() with errback attached,
        never rely on the start_urls shortcut in production spiders.
        """
        start_url = "https://twiki.cern.ch/twiki/bin/view/CMSPublic/CRAB3ConfigurationFile"
        self._base_host = urlparse(start_url).netloc
        yield Request(
            url=start_url,
            callback=self.parse,
            errback=self.errback,
        )

    def parse(self, response: Response) -> Iterator[TWikiPageItem | Request]:
        """
        Twiki pages render their main content inside #patternMain or .twikiMain.

        @url https://twiki.cern.ch/twiki/bin/view/CMSPublic/CRAB3ConfigurationFile
        @returns items 1 1
        @scrapes url title same_host_links_count
        """
        self.logger.info("Status %s for %s", response.status, response.url)

        yield from parse_twiki_page(response) # Yield item
        # then, follow links
        shlinks = same_host_links(self._base_host, response)
        logger.info("Found %d same-host links", len(shlinks))

    def errback(self, failure):
        self.logger.error(
            "Request failed: %s — %s",
            failure.request.url,
            repr(failure.value),
        )

def parse_twiki_page(response: Response) -> Iterator[TWikiPageItem]:
    # Twiki-specific selectors
    title = (
        response.css("#topic-title::text").get()
        or response.css(".patternTitle::text").get()
        or response.css("title::text").get("").split("<")[0].strip()
    )
    # Main content div — Twiki wraps body in .patternMain or #twikiMainContents
    body_text = " ".join(
        response.css("#twikiMainContents *::text, .patternMain *::text").getall()
    ).strip()


    logger.info("Found title: %r", title)

    yield TWikiPageItem(
        url=response.url,
        title=title,
        body_length=len(body_text),
        body_preview=body_text[:300],
        content_type=get_content_type(response)
    )
