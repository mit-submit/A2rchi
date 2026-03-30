import logging
from typing import Iterator
from scrapy import Spider, Request
from scrapy.http import Response
from src.data_manager.collectors.scrapers.items import WebPageItem
from src.data_manager.collectors.scrapers.spiders.link import LinkSpider
from src.data_manager.collectors.scrapers.parsers.twiki import parse_twiki_page
from scrapy.link import Link


class TwikiSpider(LinkSpider):
    """
    Minimal Twiki spider against a real Twiki target.
    Public page — no SSO needed — isolates lifecycle learning from auth complexity.
    """

    name = "twiki"
    
    _DEFAULT_START_URLS = ["https://twiki.cern.ch/twiki/bin/view/CMSPublic/CRAB3ConfigurationFile"]
    
    custom_settings = {
        "ROBOTSTXT_OBEY": False,
        "DOWNLOAD_DELAY": 60,
        "DOWNLOAD_TIMEOUT": 120,
        "RETRY_TIMES": 0,
    }

    def parse_item(self, response: Response) -> Iterator[WebPageItem]:
        """
        Twiki pages render their main content inside #patternMain or .twikiMain.
        @url https://twiki.cern.ch/twiki/bin/view/CMSPublic/CRAB3ConfigurationFile
        @returns items 1 1
        @scrapes url title
        """
        yield from parse_twiki_page(response)

    def parse_follow_links(self, response: Response) -> Iterator[Link]:
        """
        Follow links to other Twiki pages.
        @url https://twiki.cern.ch/twiki/bin/view/CMSPublic/CRAB3ConfigurationFile
        @returns requests 1
        """
        yield from super().parse_follow_links(response)
