from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse
from typing import Any, Dict, List, Optional, Callable
from collections import defaultdict

from scrapy.crawler import CrawlerProcess, Crawler
from scrapy.utils.project import get_project_settings
from scrapy.spiderloader import SpiderLoader
from scrapy.settings import Settings
from scrapy import Spider
from src.data_manager.collectors.utils import extract_urls_from_file
from src.data_manager.collectors.utils.anonymizer import Anonymizer
from src.data_manager.collectors.persistence import PersistenceService
from src.utils.config_access import get_global_config
from src.utils.logging import get_logger
from src.data_manager.collectors.utils.markitdown_convertor import MarkitdownConvertor

logger = get_logger(__name__)

_NON_SPIDER_KEYS = {"enabled", "visible", "input_lists", "urls", "sites", "fallback_spider", "domain", "domains"}

def _make_spider_loader(settings: Settings) -> Callable[[str], type[Spider]]:
    """Bind settings once, return a name → SpiderClass callable."""
    return SpiderLoader.from_settings(settings).load

def _spider_section_enabled(cfg: Dict[str, Any]) -> bool:
    """Respect web.<spider>.enabled; missing or null → enabled (on)."""
    v = cfg.get("enabled", True)
    return bool(v) if v is not None else True

class ScraperManager:
    """
    Coordinates all web crawls as a single CrawlerProcess run.

    One CrawlerProcess → one Twisted reactor → all spiders run concurrently.
    Git collection is now GitManager's responsibility.
    SSO authentication is handled by AuthDownloaderMiddleware + CERNSSOProvider.
    """

    def __init__(self, dm_config: Optional[Dict[str, Any]] = None, persistence: PersistenceService = None, anonymizer: Anonymizer = None, markitdown_manager: MarkitdownConvertor= None) -> None:
        global_config = get_global_config()
        self.data_path = Path(global_config["DATA_PATH"])
        self.persistence = persistence
        self.anonymizer = anonymizer
        self.markitdown_manager = markitdown_manager
        self.settings = Settings()
        self.settings.setmodule(
            "src.data_manager.collectors.scrapers.settings",
            priority="project",
        )

        sources_config = (dm_config or {}).get("sources", {}) or {}

        self.config = sources_config.get("web", {}) if isinstance(sources_config, dict) else {}
        self.sites_config = self.config.get("sites", {}) if isinstance(self.config, dict) else {}
        self.fallback_spider = self.config.get("fallback_spider", "link")
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
        cfg = self._effective_cfg(spider_key)
        if urls and _spider_section_enabled(cfg):
            self._add_crawler(process, SpiderClass, urls, cfg)
            # Fix Twisted/Scrapy try to installs OS signal handlers (SIGINT / SIGTERM) while the code is running in a worker thread
            process.start(install_signal_handlers=False) 

    def _run(self, url_fn: Callable[[str, Dict], List[str]]) -> None:
        if not self.enabled:
            logger.info("Web scraping disabled; skipping")
            return
        process = CrawlerProcess(self.settings)
        load_spider = _make_spider_loader(self.settings)
        (self.data_path / "websites").mkdir(parents=True, exist_ok=True)

        domain_map = self._build_domain_map()
        all_urls = url_fn()               # <-- no longer per-spider
        buckets = self._route_urls(all_urls, domain_map)

        added = False
        for spider_key, urls in buckets.items():
            site_cfg = self._effective_cfg(spider_key)
            if urls and _spider_section_enabled(site_cfg):
                try:
                    SpiderClass = load_spider(spider_key)
                except KeyError:
                    continue
                self._add_crawler(process, SpiderClass, urls, site_cfg)
                added = True
        if added:
            process.start(install_signal_handlers=False) 

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
        crawler.settings.set("ANONYMIZER_SERVICE", self.anonymizer, priority="spider")
        crawler.settings.set("MARKITDOWN_SERVICE", self.markitdown_manager, priority="spider")
        process.crawl(crawler, start_urls=urls, **cfg)

    # ── URL sources & list parsing ──────────────────────────────────────────────────────

    def _config_urls(self) -> List[str]:
        return self._collect_global_urls()

    def _catalog_urls(self) -> List[str]:
        if not self.persistence:
            return []

        metadata = self.persistence.catalog.get_metadata_by_filter(
            "source_type", source_type="web", metadata_keys=["url", "spider_name"]
        )
        return [ m[1].get("url", "").strip() for m in metadata if m[1].get("url")]
    
    def _collect_global_urls(self) -> List[str]:
        urls = list(self.config.get("urls") or [])
        for list_path in self.config.get("input_lists") or []:
            path = Path("weblists") / Path(list_path).name
            urls.extend(extract_urls_from_file(path))
        return urls
    
    def _build_domain_map(self) -> Dict[str, str]:
        """
        Builds a mapping of domains to spider keys.
        """
        return {
            host: spider_key
            for spider_key, cfg in self.sites_config.items()
            if isinstance(cfg, dict)
            for d in [cfg.get("domain")] + (cfg.get("domains") or [])
            if d
            for host in [urlparse(f"https://{d}").hostname or d.lower().strip().rstrip(".")]
        }
    
    def _route_urls(self, urls: List[str], domain_map: Dict[str, str]) -> Dict[str, List[str]]:
        """
        Partitions URL pool into {spider_key: [urls]} buckets.
        """
        buckets: Dict[str, List[str]] = defaultdict(list)
        for url in urls:
            host = urlparse(url).hostname
            if host:
                host = host.lower().rstrip(".")
            spider_key = domain_map.get(host, self.fallback_spider) if host else self.fallback_spider
            buckets[spider_key].append(url)
        return dict(buckets)

    def _effective_cfg(self, spider_key: str) -> Dict[str, Any]:
        """
        Returns the effective configuration for a spider, merging global defaults with site-specific overrides.
        """
        merged = {**self.config, **(self.sites_config.get(spider_key) or {})}
        for k in _NON_SPIDER_KEYS:
            merged.pop(k, None)
        return merged