import scrapy
from urllib.parse import urlparse


class TwikiSpider(scrapy.Spider):
    """
    Minimal Twiki spider against a real Twiki target.
    Public page — no SSO needed — isolates lifecycle learning from auth complexity.
    """

    name = "twiki"

    custom_settings = {
        "ROBOTSTXT_OBEY": False,
    }

    async def start(self):
        """
        Seed request for the CRAB3 Twiki config page.
        Building the habit: always use start_requests() with errback attached,
        never rely on the start_urls shortcut in production spiders.
        """
        yield scrapy.Request(
            url="https://twiki.cern.ch/twiki/bin/view/CMSPublic/CRAB3ConfigurationFile",
            callback=self.parse,
            errback=self.errback,
            meta={"source_type": "web"},  # will become "sso" for protected Twiki pages
        )

    def parse(self, response):
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

        # Same-host links
        base = "twiki.cern.ch"
        same_host_links = [
            response.urljoin(href)
            for href in response.css("a::attr(href)").getall()
            if urlparse(response.urljoin(href)).netloc == base
        ]

        self.logger.info("Found title: %r", title)
        self.logger.info("Found %d same-host links", len(same_host_links))

        yield {
            "url": response.url,
            "title": title,
            "body_length": len(body_text),
            "body_preview": body_text[:300],
            "same_host_links_count": len(same_host_links),
            "same_host_links_sample": same_host_links[:5],
            "source_type": response.meta.get("source_type"),
            "content_type": response.headers.get("Content-Type", b"").decode(),
        }

    def errback(self, failure):
        self.logger.error(
            "Request failed: %s — %s",
            failure.request.url,
            repr(failure.value),
        )
