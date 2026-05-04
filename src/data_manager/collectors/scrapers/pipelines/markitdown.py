from scrapy import Spider
from src.utils.logging import get_logger
from src.data_manager.collectors.utils.markitdown_convertor import MarkitdownConvertor
from src.data_manager.collectors.utils.anonymizer import Anonymizer
from src.data_manager.collectors.scrapers.pipelines.anonymization import AnonymizationPipeline
from src.data_manager.collectors.scrapers.items import BasePageItem
from scrapy.exceptions import NotConfigured

logger = get_logger(__name__)

class MarkitdownPipeline:
    """Runs at priority 250, before PersistencePipeline (300)."""

    def __init__(self, markitdown: MarkitdownConvertor, anonymizer: Anonymizer, anonymize_data: bool):
        self._markitdown = markitdown
        self._anonymizer = anonymizer
        self._anonymize_data = anonymize_data

    @classmethod
    def from_crawler(cls, crawler):
        enabled = crawler.settings.getbool("MARKITDOWN_ENABLED", True)
        markitdown_convertor = crawler.settings.get("MARKITDOWN_SERVICE")
        anonymizer = crawler.settings.get("ANONYMIZER_SERVICE")
        anonymize_data = crawler.settings.getbool("ANONYMIZE_DATA", True)
        if not enabled:
            raise NotConfigured("Markitdown is disabled")
        if markitdown_convertor is None:
            # when we use scrapy cmd, we don't have the markitdown service provided
            markitdown_convertor = MarkitdownConvertor()
        if anonymizer is None:
            # when we use scrapy cmd, we don't have the anonymizer service provided
            anonymizer = AnonymizationPipeline.from_crawler(crawler)._anonymizer
        return cls(markitdown=markitdown_convertor, anonymizer=anonymizer, anonymize_data=anonymize_data)

    def process_item(self, item: BasePageItem, spider: Spider) -> BasePageItem:
        if isinstance(item.get("content"), str):
            logger.info(f"Converting content to markdown: {item['content']}")
            item["content"] = self._markitdown.convert(item["content"], file_extension=item["suffix"])
            if self._anonymize_data:
                logger.info(f"Anonymizing content: {item['content']}")
                item["content"] = self._anonymizer.anonymize(item["content"])
            logger.info(f"Markitdown result ({'anonymized' if self._anonymize_data else 'not second pass anonymized'})): {item['content']}")
        return item