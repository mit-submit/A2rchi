from typing import TYPE_CHECKING

from src.data_manager.collectors.utils.anonymizer import Anonymizer
from src.data_manager.collectors.scrapers.items import BasePageItem

from scrapy import Spider
from src.utils.logging import get_logger

logger = get_logger(__name__)

class AnonymizationPipeline:
    """Runs at priority 250, before PersistencePipeline (300)."""

    _DEFAULT_ANONYMIZER_CONFIG = {
        "utils": {
            "anonymizer": {
                "nlp_model": "en_core_web_sm",
                "excluded_words": ["John", "Jane", "Doe"],
                "greeting_patterns": [
                    r"^(hi|hello|hey|greetings|dear)\b",
                    r"^\w+,\s*",
                ],
                "signoff_patterns": [
                    r"\b(regards|sincerely|best regards|cheers|thank you)\b",
                    r"^\s*[-~]+\s*$",
                ],
                "email_pattern": r"[\w\.-]+@[\w\.-]+\.\w+",
                "username_pattern": r"\[~[^\]]+\]",
            }
        }
    }

    def __init__(self, anonymizer: Anonymizer) -> None:
        self._anonymizer = anonymizer

    @classmethod
    def from_crawler(cls, crawler):
        enabled = crawler.settings.getbool("ANONYMIZE_DATA", True)
        anonymizer = crawler.settings.get("ANONYMIZER_SERVICE")
        if not enabled:
            raise NotConfigured("Anonymization is disabled")
        if anonymizer is None:
            # when we use scrapy cmd, we don't have the anonymizer service provided
            dm_config = cls._DEFAULT_ANONYMIZER_CONFIG
            return cls(anonymizer=Anonymizer(dm_config))
        return cls(anonymizer=anonymizer)

    def process_item(self, item: BasePageItem, spider: Spider) -> BasePageItem:
        if isinstance(item.get("content"), str):
            logger.debug(f"Anonymizing content: {item['content']}")
            item["content"] = self._anonymizer.anonymize_markup(item["content"])
            logger.debug(f"Anonymized content: {item['content']}")
        if isinstance(item.get("title"), str):
            item["title"] = self._anonymizer.anonymize(item["title"])
        return item
