"""
Persistence pipeline: converts Scrapy Items → ScrapedResource → PersistenceService.

Design notes
------------
* Follows Scrapy's canonical ``from_crawler`` injection pattern.
  The ``PersistenceService`` instance and output directory are set on
  ``crawler.settings`` *programmatically* by ``ScraperManager`` before the
  crawl starts — they are live Python objects, not serialised config values,
  so they must never appear in settings.py or YAML.

* SRP boundary: this pipeline does *two* things (adapt + persist). That is
  intentional and acceptable because the two operations are trivially coupled
  here (no branching logic in either). If adapter logic grows, extract it to
  ``adapters/resource_adapter.py`` and import here.

* The pipeline never raises — it logs and drops items on error so a single
  bad page does not kill the crawl (OR-5 / FR-7b).

Settings keys consumed
----------------------
PERSISTENCE_SERVICE   : PersistenceService instance (required)
PERSISTENCE_OUTPUT_DIR: pathlib.Path  — where files are written (required)
"""

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from scrapy import Spider
from scrapy.exceptions import NotConfigured

from src.data_manager.collectors.scrapers.adapters import to_scraped_resource

if TYPE_CHECKING:
    from scrapy import Crawler
    from src.data_manager.collectors.persistence import PersistenceService

logger = logging.getLogger(__name__)

SETTING_SERVICE = "PERSISTENCE_SERVICE"
SETTING_OUTPUT_DIR = "PERSISTENCE_OUTPUT_DIR"

class PersistencePipeline:
    """
    Scrapy item pipeline that persists scraped items via ``PersistenceService``.

    Activation (in ScraperManager, before CrawlerProcess/Runner starts)::

        crawler.settings.set(
            "PERSISTENCE_SERVICE", persistence_service_instance, priority="spider"
        )
        crawler.settings.set(
            "PERSISTENCE_OUTPUT_DIR", Path("/root/data/websites"), priority="spider"
        )
        crawler.settings.set(
            "ITEM_PIPELINES",
            {"src.data_manager.collectors.scrapers.pipelines.PersistencePipeline": 300},
            priority="spider",
        )
    """

    def __init__(self, persistence: "PersistenceService", output_dir: Path) -> None:
        self._persistence = persistence
        self._output_dir = output_dir
        self._success_count = 0
        self._error_count = 0

    # ------------------------------------------------------------------
    # Scrapy lifecycle
    # ------------------------------------------------------------------

    @classmethod
    def from_crawler(cls, crawler: "Crawler") -> "PersistencePipeline":
        """Canonical Scrapy injection point — pulls service from settings."""
        persistence = crawler.settings.get(SETTING_SERVICE)
        output_dir = crawler.settings.get(SETTING_OUTPUT_DIR)

        if persistence is None:
            raise NotConfigured(
                f"PersistencePipeline requires '{SETTING_SERVICE}' in crawler settings. "
                "Set it programmatically in ScraperManager before starting the crawl."
            )
        if output_dir is None:
            raise NotConfigured(
                f"PersistencePipeline requires '{SETTING_OUTPUT_DIR}' in crawler settings."
            )

        instance = cls(persistence=persistence, output_dir=Path(output_dir))
        return instance

    def open_spider(self, spider: Spider) -> None:
        self._output_dir.mkdir(parents=True, exist_ok=True)
        logger.info(
            "PersistencePipeline opened | output_dir=%s", self._output_dir
        )

    def close_spider(self, spider: Spider) -> None:
        # Summary logged via spider_closed signal too, but belt-and-suspenders here.
        logger.info(
            "PersistencePipeline | spider=%s persisted=%d errors=%d",
            spider.name,
            self._success_count,
            self._error_count,
        )

    def process_item(self, item, spider: Spider):
        """
        Convert item → ScrapedResource → persist.

        Never raises; errors are logged and the item is dropped.
        Returning the item allows other downstream pipelines to receive it.
        """
        try:
            resource = to_scraped_resource(item)
            resource.source_type = "web"
            resource.metadata["spider_name"] = spider.name
        except Exception as exc:
            self._error_count += 1
            logger.warning(
                "Adapter failed for item from %s: %s | item=%r",
                spider.name,
                exc,
                dict(item),
                exc_info=False,  # keep log concise; set True for debug
            )
            return item  # drop from persistence but don't crash

        try:
            file_path = self._persistence.persist_resource(resource, self._output_dir)
            self._success_count += 1
            logger.debug(
                "Persisted %s → %s", resource.get_hash(), file_path
            )
        except Exception as exc:
            self._error_count += 1
            logger.error(
                "PersistenceService.persist_resource failed for %s: %s",
                getattr(resource, "url", "unknown"),
                exc,
                exc_info=True,
            )

        return item