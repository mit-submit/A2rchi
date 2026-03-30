from typing import Iterator
from urllib.parse import urlparse
from scrapy.http import Response, Request
from src.data_manager.collectors.scrapers.items import WebPageItem
from src.data_manager.collectors.scrapers.spiders.link import LinkSpider
from src.data_manager.collectors.scrapers.parsers.twiki import parse_twiki_page


class TwikiSpider(LinkSpider):
    """
    Minimal Twiki spider against a real Twiki target.
    Public page — no SSO needed — isolates lifecycle learning from auth complexity.
    """

    name = "twiki"
    
    _DEFAULT_START_URLS = [
        "https://twiki.cern.ch/twiki/bin/view/CMSPublic/SWGuide"
    ]

    _DEFAULT_DENY = [
        "/bin/edit",
        "/bin/logon", 
        "/bin/oops",
        "/bin/attach",
        "/bin/search",
        "/bin/rdiff",
        "/bin/history",
        "/bin/raw",
        "/LeftBarLeftBar",
    ]

    custom_settings = {
        "ROBOTSTXT_OBEY": False,
        "DOWNLOAD_TIMEOUT": 120,
        "RETRY_TIMES": 0, # Very Safe no retries
        "DEPTH_LIMIT": 1, # Default max depth
        "DOWNLOAD_DELAY": 60, # Default (download) delay
        "CLOSESPIDER_PAGECOUNT": 1 # Very Safe Default max pages
    }

    @staticmethod
    def _normalize_url(url: str) -> str:
        """Keep TWiki URLs clean: only scheme + netloc + path — drop query params and fragment."""
        return urlparse(url)._replace(query="", fragment="").geturl() # type: ignore

    _DEFAULT_PROCESS_VALUE = _normalize_url

    def parse(self, response: Response) -> Iterator[WebPageItem | Request]:
        """
        Twiki pages render their main content inside #patternMain or .twikiMain.
        @url https://twiki.cern.ch/twiki/bin/view/CMSPublic/SWGuide
        @returns items 1 1
        @scrapes url title
        @returns requests 110 110
        """
        yield from super().parse(response)

    def parse_item(self, response: Response) -> Iterator[WebPageItem]:
        yield from parse_twiki_page(response)