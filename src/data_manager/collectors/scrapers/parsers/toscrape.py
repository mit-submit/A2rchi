from typing import Iterator
from scrapy.http import Response, TextResponse
from src.data_manager.collectors.scrapers.items import WebPageItem
from src.data_manager.collectors.scrapers.utils import get_content_type

def parse_toscrape_page(response: Response) -> Iterator[WebPageItem]:
    ct = get_content_type(response)

    if response.url.lower().endswith(".pdf") or "application/pdf" in ct:
        yield WebPageItem(
            url=response.url,
            content=response.body,
            suffix="pdf",
            title="",
            content_type=ct,
        )
        return

    title = response.css("title::text").get(default="").strip()
    encoding = response.encoding if isinstance(response, TextResponse) else "utf-8"

    yield WebPageItem(
        url=response.url,
        content=response.text,
        suffix="html",
        title=title,
        content_type=ct,
        encoding=encoding,
    )