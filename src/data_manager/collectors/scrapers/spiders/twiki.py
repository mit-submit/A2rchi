from typing import Iterator
from urllib.parse import urlparse
from scrapy.http import Response, Request
from src.data_manager.collectors.scrapers.items import WebPageItem
from src.data_manager.collectors.scrapers.spiders.link import LinkSpider
from src.data_manager.collectors.scrapers.parsers.twiki import parse_twiki_page


class TwikiSpider(LinkSpider):
    """
    Minimal Twiki spider against a real Twiki target.
    Support CERN SSO authentication.
    """

    name = "twiki"
    
    auth_provider_name = "cern_sso"
    
    _DEFAULT_START_URLS = [
        "https://twiki.cern.ch/twiki/bin/view/CMS/HeavyIons",      # private page
        "https://twiki.cern.ch/twiki/bin/view/CMSPublic/SWGuide", # public page
    ]

    _DEFAULT_DENY = [
        # CGI endpoints — no content, mostly we allow just /bin/view/ or /bin/viewauth/
        r"/bin/edit",
        r"/bin/logon", 
        r"/bin/oops",
        r"/bin/attach",
        r"/bin/search",
        r"/bin/rdiff",
        r"/bin/history",
        r"/bin/raw",
        r"/bin/genpdf",      # PDF generation — not content
        r"/bin/view/Main",   # user profile pages, not content
        # Navigation/structural pages
        r"LeftBarLeftBar",
        r"/bin/view/[^/]+/WebLeftBar", # sidebar navigation template
        r"/bin/view/[^/]+/WebTopBar", # top navigation bar
        r"/bin/view/[^/]+/WebChanges", # recent changes — floods with links
        r"/bin/view/[^/]+/WebIndex", # alphabetical index — floods with links
        r"/bin/view/[^/]+/WebStatistics", # statistics pages
        r"/bin/view/[^/]+/WebNotify", # notification subscriptions
        r"/bin/view/[^/]+/WebPreferences", # wiki preferences
        # Discard Topic List page, too many links in https://twiki.cern.ch/twiki/bin/view/CMSPublic/WebTopicList
        r"/bin/view/[^/]+/WebTopicList", # too many links, or should been put as seeds_urls.
        r"/bin/view/[^/]+/WebSearch",  # search page — floods with links
        r"/bin/view/[^/]+/WebChanges", # recent changes — floods with links
    ]

    custom_settings = {
        "ROBOTSTXT_OBEY": False,
        "DOWNLOAD_TIMEOUT": 120,
        "RETRY_TIMES": 0, # Very Safe no retries
        "DEPTH_LIMIT": 1, # Default max depth
        "DOWNLOAD_DELAY": 60, # Default (download) delay
        "CLOSESPIDER_PAGECOUNT": 1, # Very Safe Default max pages
        "COOKIES_ENABLED": False,      # disable CookiesMiddleware jar
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
        @returns requests 1 100
        """
        yield from super().parse(response)

    def parse_item(self, response: Response) -> Iterator[WebPageItem]:
        yield from parse_twiki_page(response)