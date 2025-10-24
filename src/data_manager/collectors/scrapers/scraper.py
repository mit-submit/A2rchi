from typing import List

import requests
import re

from src.data_manager.collectors.scrapers.scraped_resource import \
    ScrapedResource
from src.utils.logging import get_logger

logger = get_logger(__name__)


class WebScraper:
    """Simple HTTP scraper that fetches raw content from public URLs."""

    def __init__(self, verify_urls: bool = True, enable_warnings: bool = True) -> None:
        self.verify_urls = verify_urls
        self.enable_warnings = enable_warnings

    def scrape(self, url: str) -> List[ScrapedResource]:
        """Fetch the given URL and return the retrieved payload.

        The caller is responsible for deciding how to persist the payload.
        """
        if not self.enable_warnings:
            import urllib3  # imported lazily to avoid the dependency when unused

            urllib3.disable_warnings()

        logger.info(f"Fetching URL {url}")
        response = requests.get(url, verify=self.verify_urls)
        response.raise_for_status()

        content_type = response.headers.get("Content-Type")
        if url.lower().endswith(".pdf"):
            resource = ScrapedResource(
                url=url,
                content=response.content,
                suffix="pdf",
                source_type="links",
                metadata={"content_type": content_type},
            )
        else:
            title = re.findall(r'<title>(.*)<\/title>',response.text)
            resource = ScrapedResource(
                url=url,
                content=response.text,
                suffix="html",
                source_type="links",
                metadata={
                    "content_type": content_type,
                    "encoding": response.encoding,
                    "title": title[0] if len(title)>0 else url
                },
            )

        return [resource]