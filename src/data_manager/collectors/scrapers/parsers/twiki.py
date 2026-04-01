import logging
from typing import Iterator
from scrapy.http import Response, TextResponse
from src.utils.logging import get_logger
from src.data_manager.collectors.scrapers.items import WebPageItem
from src.data_manager.collectors.scrapers.utils import get_content_type

logger = get_logger(__name__)

_TWIKI_BODY_SELECTORS = [
    "body.patternViewPage #patternMainContents",
    "#patternMainContents",
    "body.patternViewPage #patternMain",
    "#patternMain",
    "#twikiMainContents",
    ".patternViewBody",
    ".twikiTopicText",
    ".patternTopic",
    ".patternContent",
    ".patternMain",  # class variant, rare
]
def _extract_twiki_body(response: Response) -> str:
    for selector in _TWIKI_BODY_SELECTORS:
        text = " ".join(response.css(f"{selector} *::text").getall()).strip()
        if text:
            return text
    return ""

def parse_twiki_page(response: Response) -> Iterator[WebPageItem]:
    if not isinstance(response, TextResponse):
        logger.debug("Skipping non-text response (no css): %s", response.url)
        return
    # Twiki-specific selectors
    title = (
        response.css("#topic-title::text").get()
        or response.css(".patternTitle::text").get()
        or response.css("title::text").get("").split("<")[0].strip()
    )
    # Main content div — Twiki wraps body in .patternMain or #twikiMainContents
    body_text = _extract_twiki_body(response)
    if not body_text:
        logger.debug("No body text found in Twiki page: %s", response.url)
        return
    yield WebPageItem(
        url=response.url,
        title=title,
        content=body_text,
        suffix="html",
        source_type="web",
        content_type=get_content_type(response),
        encoding=response.encoding or "utf-8",
    )