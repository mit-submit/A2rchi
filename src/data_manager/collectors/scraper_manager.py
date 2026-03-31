from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Callable

from scrapy.crawler import CrawlerProcess, Crawler
from scrapy.utils.project import get_project_settings
from scrapy.spiderloader import SpiderLoader
from scrapy.settings import Settings
from scrapy import Spider
from src.data_manager.collectors.persistence import PersistenceService
from src.utils.config_access import get_global_config
from src.utils.logging import get_logger

logger = get_logger(__name__)

def _make_spider_loader(settings: Settings) -> Callable[[str], type[Spider]]:
    """Bind settings once, return a name → SpiderClass callable."""
    return SpiderLoader.from_settings(settings).load

class ScraperManager:
    """
    Coordinates all web crawls as a single CrawlerProcess run.

    One CrawlerProcess → one Twisted reactor → all spiders run concurrently.
    Git collection is now GitManager's responsibility.
    SSO authentication is handled by AuthDownloaderMiddleware + CERNSSOProvider.
    """

    def __init__(self, dm_config: Optional[Dict[str, Any]] = None, persistence: PersistenceService = None) -> None:
        global_config = get_global_config()
        self.data_path = Path(global_config["DATA_PATH"])
        self.persistence = persistence
        self.settings = Settings()
        self.settings.setmodule(
            "src.data_manager.collectors.scrapers.settings",
            priority="project",
        )

        sources_config = (dm_config or {}).get("sources", {}) or {}

        logger.info("sources_config: %s", json.dumps(sources_config, indent=2, default=str))
        self.config = sources_config.get("web", {}) if isinstance(sources_config, dict) else {}
        self.enabled = self.config.get("enabled", True)

    # ── Public interface ──────────────────────────────────────────────────────

    def collect_all_from_config(self) -> None:
        logger.info("collect_all_from_config")
        self._run(self._config_urls)

    def schedule_collect(self, last_run: Optional[str] = None) -> None:
        self._run(self._catalog_urls)
    
    def collect(self, spider_key: str, urls: List[str]) -> None:
        process = CrawlerProcess(self.settings)
        logger.info("project_settings: %s", json.dumps(self.settings, indent=2, default=str))
        try:
            SpiderClass = _make_spider_loader(self.settings)(spider_key)
        except KeyError:
            logger.error("Unknown spider: %s", spider_key)
            return
        cfg = self.config.get(spider_key, {})   # use config settings if present, else defaults
        if urls:
            self._add_crawler(process, SpiderClass, urls, cfg)
            process.start()

    def _run(self, url_fn: Callable[[str, Dict], List[str]]) -> None:
        if not self.enabled:
            logger.info("Web scraping disabled; skipping")
            return
        process = CrawlerProcess(self.settings)
        load_spider = _make_spider_loader(self.settings)
        (self.data_path / "websites").mkdir(parents=True, exist_ok=True)

        added = False
        for spider_key, cfg in self.config.items():
            logger.info("spider_key: %s, cfg: %s", spider_key, json.dumps(cfg, indent=2, default=str))
            if not isinstance(cfg, dict):
                continue
            try:
                SpiderClass = load_spider(spider_key)
            except KeyError:
                continue
            urls = url_fn(spider_key, cfg)
            logger.info("urls: %s", urls)
            if urls:
                self._add_crawler(process, SpiderClass, urls, cfg)
                added = True
        logger.info("added: %s", added)
        if added:
            process.start()

    # ── CrawlerProcess wiring ─────────────────────────────────────────────────

    def _add_crawler(
        self,
        process: CrawlerProcess,
        spider_class: type[Spider],
        urls: List[str],
        cfg: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Create a Crawler for spider_key, inject PersistencePipeline settings,
        and register it with the process.
        """
        cfg = cfg or {}
        crawler: Crawler = process.create_crawler(spider_class)
        # Inject persistence objects — live Python instances, must be priority="spider"
        crawler.settings.set("PERSISTENCE_SERVICE", self.persistence, priority="spider")
        crawler.settings.set("PERSISTENCE_OUTPUT_DIR", self.data_path / "websites", priority="spider")
        process.crawl(crawler, start_urls=urls, **cfg)

    # ── URL sources & list parsing ──────────────────────────────────────────────────────

    def _config_urls(self, spider_key: str, cfg: Dict) -> List[str]:
        urls = list(cfg.get("urls") or [])
        logger.info("cfg_urls: urls: %s", urls)
        for list_path in cfg.get("input_lists") or []:
            path = Path(list_path)
            if not path.exists():
                logger.warning("Input list not found: %s", path)
                continue
            urls.extend(self._extract_urls_from_file(path))
        return urls

    def _catalog_urls(self, spider_key: str, cfg: Dict) -> List[str]:
        if not self.persistence:
            return []

        logger.info("catalog_urls: spider_key: %s, cfg: %s", spider_key, json.dumps(cfg, indent=2, default=str))
        metadata = self.persistence.catalog.get_metadata_by_filter(
            "source_type", source_type="web", metadata_keys=["url", "spider_name"]
        )
        return [
            m[1].get("url", "").strip()
            for m in metadata
            if m[1].get("spider_name", "link") == spider_key and m[1].get("url")
        ]

    def _extract_urls_from_file(self, path: Path) -> List[str]:
        urls: List[str] = []
        with path.open("r") as f:
            for line in f:
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                urls.append(stripped.split(",")[0].strip())
        return urls