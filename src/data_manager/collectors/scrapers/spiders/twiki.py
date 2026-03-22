from typing import Iterator, cast
from scrapy import Spider, Request
from scrapy.http import Response
from urllib.parse import urlparse
from src.data_manager.collectors.scrapers.items import TestTWikiItem
from src.data_manager.collectors.scrapers.utils import get_content_type, same_host_links


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
        base_host = urlparse(start_url).netloc
        yield Request(
            url=start_url,
            callback=self.parse,
            errback=self.errback,
            meta={
                "source_type": "web",
                "base_host": base_host,
            },
        )

    def parse(self, response: Response) -> Iterator[TestTWikiItem]:
        """
        Twiki pages render their main content inside #patternMain or .twikiMain.

        @url https://twiki.cern.ch/twiki/bin/view/CMSPublic/CRAB3ConfigurationFile
        @returns items 1 1
        @scrapes url title same_host_links_count
        """
        self.logger.info("Status %s for %s", response.status, response.url)

        # Twiki-specific selectors
        title = response.css("#topic-title::text, .patternTitle::text").get(default="")
        if not title:
            title = response.css("title::text").get(default="").replace(" < TWiki", "").strip()

        # Main content div — Twiki wraps body in .patternMain or #twikiMainContents
        body_text = " ".join(
            response.css("#twikiMainContents *::text, .patternMain *::text").getall()
        ).strip()

        shlinks = same_host_links(response.meta['base_host'], response)

        self.logger.info("Found title: %r", title)
        self.logger.info("Found %d same-host links", len(shlinks))

        yield TestTWikiItem(
            url=response.url,
            title=title,
            body_length=len(body_text),
            body_preview=body_text[:300],
            same_host_links_count=len(shlinks),
            same_host_links_sample=shlinks[:5],
            source_type=response.meta.get("source_type"),
            content_type=get_content_type(response)
        )

    def errback(self, failure):
        self.logger.error(
            "Request failed: %s — %s",
            failure.request.url,
            repr(failure.value),
        )
