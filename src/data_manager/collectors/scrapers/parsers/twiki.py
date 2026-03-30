from typing import Iterator
from scrapy.http import Response
from src.data_manager.collectors.scrapers.items import WebPageItem
from src.data_manager.collectors.scrapers.utils import get_content_type


def parse_twiki_page(response: Response) -> Iterator[WebPageItem]:
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

    yield WebPageItem(
        url=response.url,
        title=title,
        content=body_text,
        suffix="html",
        content_type=get_content_type(response),
    )