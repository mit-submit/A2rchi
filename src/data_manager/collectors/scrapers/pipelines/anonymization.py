from typing import TYPE_CHECKING

from src.data_manager.collectors.utils.anonymizer import Anonymizer
from src.data_manager.collectors.scrapers.items import BasePageItem

from scrapy import Spider


class AnonymizationPipeline:
    """Runs at priority 250, before PersistencePipeline (300)."""

    def __init__(self, anonymizer: Anonymizer) -> None:
        self._anonymizer = anonymizer

    @classmethod
    def from_crawler(cls, crawler):
        enabled = crawler.settings.getbool("ANONYMIZE_DATA", True)
        if not enabled:
            return cls(anonymizer=None)
        return cls(anonymizer=Anonymizer()) # type: ignore

    def process_item(self, item: BasePageItem, spider: Spider) -> BasePageItem:
        if self._anonymizer is not None:
            if isinstance(item.get("content"), str):
                item["content"] = self._anonymizer.anonymize(item["content"])
            if isinstance(item.get("title"), str):
                item["title"] = self._anonymizer.anonymize(item["title"])
        return item
