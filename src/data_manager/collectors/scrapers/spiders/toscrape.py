from typing import Iterator
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

    def parse_item(self, response: Response) -> Iterator[WebPageItem]:
        """
        @url https://quotes.toscrape.com/
        @returns items 1
        @scrapes url title
        """
        yield from parse_toscrape_page(response)

    def parse_follow_links(self, response: Response) -> Iterator[Link]:
        """
        @url https://quotes.toscrape.com/
        @returns requests 1
        """
        yield from super().parse_follow_links(response)
