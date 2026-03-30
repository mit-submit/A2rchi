from typing import Iterator
from scrapy import Request
from scrapy.http import Response
from src.data_manager.collectors.scrapers.items import WebPageItem
from src.data_manager.collectors.scrapers.spiders.link import LinkSpider
from src.data_manager.collectors.scrapers.parsers.toscrape import parse_toscrape_page
from scrapy.link import Link

class ToscrapeSpider(LinkSpider):
    """
    Spider for scraping HTML pages from toscrape.com.
    """

    name = "toscrape"

    _DEFAULT_START_URLS = ["https://quotes.toscrape.com/"]

    def parse(self, response: Response) -> Iterator[WebPageItem | Request]:
        """
        @url https://quotes.toscrape.com/
        @returns items 1
        @returns requests 1
        @scrapes url title
        """
        yield from super().parse(response)

    def parse_item(self, response: Response) -> Iterator[WebPageItem]:
        yield from parse_toscrape_page(response)
