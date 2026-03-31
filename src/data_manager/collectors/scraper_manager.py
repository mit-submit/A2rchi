from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Type

from scrapy.crawler import CrawlerProcess, Crawler
from scrapy.utils.project import get_project_settings

from src.data_manager.collectors.persistence import PersistenceService
from src.utils.config_access import get_global_config
from src.utils.logging import get_logger

logger = get_logger(__name__)

# Spider registry — add new spider classes here, nothing else changes
_SPIDER_REGISTRY: Dict[str, str] = {
    "link":  "src.data_manager.collectors.scrapers.spiders.link.LinkSpider",
    "twiki": "src.data_manager.collectors.scrapers.spiders.twiki.TwikiSpider",
}


def _import_spider(dotted_path: str):
    module_path, cls_name = dotted_path.rsplit(".", 1)
    import importlib
    return getattr(importlib.import_module(module_path), cls_name)


class ScraperManager:
    """
    Coordinates all web crawls as a single CrawlerProcess run.

    One CrawlerProcess → one Twisted reactor → all spiders run concurrently.
    Git collection is now GitManager's responsibility.
    SSO authentication is handled by AuthDownloaderMiddleware + CERNSSOProvider.
    """

    def __init__(self, dm_config: Optional[Dict[str, Any]] = None) -> None:
        global_config = get_global_config()
        self.data_path = Path(global_config["DATA_PATH"])

        sources_config = (dm_config or {}).get("sources", {}) or {}
        links_config = sources_config.get("links", {}) if isinstance(sources_config, dict) else {}

        self.config = links_config if isinstance(links_config, dict) else {}
        self.enabled = self.config.get("enabled", True)
        self.input_lists: List[str] = self.config.get("input_lists", [])

        # Per-spider kwargs forwarded from config
        self.max_depth: Optional[int] = self.config.get("max_depth")
        self.max_pages: Optional[int] = self.config.get("max_pages")
        self.delay: Optional[int] = self.config.get("download_delay")

    # ── Public interface ──────────────────────────────────────────────────────

    def collect_all_from_config(self, persistence: PersistenceService) -> None:
        if not self.enabled:
            logger.info("Web scraping disabled; skipping")
            return

        link_urls, sso_urls = self._collect_urls_from_lists_by_type(self.input_lists)
        self._run_crawl(persistence, link_urls=link_urls, sso_urls=sso_urls)

    def collect_links(
        self,
        persistence: PersistenceService,
        link_urls: Optional[List[str]] = None,
    ) -> None:
        if not link_urls:
            return
        self._run_crawl(persistence, link_urls=link_urls)

    def collect_sso(
        self,
        persistence: PersistenceService,
        sso_urls: Optional[List[str]] = None,
    ) -> None:
        if not sso_urls:
            return
        self._run_crawl(persistence, sso_urls=sso_urls)

    def schedule_collect_links(
        self, persistence: PersistenceService, last_run: Optional[str] = None
    ) -> None:
        metadata = persistence.catalog.get_metadata_by_filter(
            "source_type", source_type="web", metadata_keys=["url"]
        )
        urls = [m[1].get("url", "").strip() for m in metadata if m[1].get("url")]
        self.collect_links(persistence, link_urls=urls)

    def schedule_collect_sso(
        self, persistence: PersistenceService, last_run: Optional[str] = None
    ) -> None:
        metadata = persistence.catalog.get_metadata_by_filter(
            "source_type", source_type="sso", metadata_keys=["url"]
        )
        urls = [m[1].get("url", "").strip() for m in metadata if m[1].get("url")]
        self.collect_sso(persistence, sso_urls=urls)

    # ── CrawlerProcess wiring ─────────────────────────────────────────────────

    def _run_crawl(
        self,
        persistence: PersistenceService,
        link_urls: Optional[List[str]] = None,
        sso_urls: Optional[List[str]] = None,
    ) -> None:
        """Build one CrawlerProcess, add all spiders, start the reactor."""
        websites_dir = self.data_path / "websites"
        websites_dir.mkdir(parents=True, exist_ok=True)

        scrapy_settings = get_project_settings()
        process = CrawlerProcess(scrapy_settings)

        if link_urls:
            self._add_crawler(
                process,
                spider_key="link",
                persistence=persistence,
                output_dir=websites_dir,
                start_urls=link_urls,
            )

        if sso_urls:
            self._add_crawler(
                process,
                spider_key="link",
                persistence=persistence,
                output_dir=websites_dir / "sso",
                start_urls=sso_urls,
                auth_provider_name="cern_sso",
            )

        if not process._crawlers:
            logger.info("No URLs to crawl; skipping reactor start")
            return

        logger.info("Starting CrawlerProcess with %d spider(s)", len(process._crawlers))
        process.start()   # blocks until all spiders finish
        logger.info("CrawlerProcess finished")

    def _add_crawler(
        self,
        process: CrawlerProcess,
        spider_key: str,
        persistence: PersistenceService,
        output_dir: Path,
        **spider_kwargs,
    ) -> None:
        """
        Create a Crawler for spider_key, inject PersistencePipeline settings,
        and register it with the process.
        """
        SpiderClass = _import_spider(_SPIDER_REGISTRY[spider_key])
        crawler: Crawler = process.create_crawler(SpiderClass)

        # Inject persistence objects — live Python instances, must be priority="spider"
        crawler.settings.set("PERSISTENCE_SERVICE", persistence, priority="spider")
        crawler.settings.set("PERSISTENCE_OUTPUT_DIR", output_dir, priority="spider")
        crawler.settings.set(
            "ITEM_PIPELINES",
            {"src.data_manager.collectors.scrapers.pipelines.PersistencePipeline": 300},
            priority="spider",
        )

        # Forward crawl tuning args if configured
        if self.max_depth is not None:
            spider_kwargs.setdefault("max_depth", self.max_depth)
        if self.max_pages is not None:
            spider_kwargs.setdefault("max_pages", self.max_pages)
        if self.delay is not None:
            spider_kwargs.setdefault("delay", self.delay)

        process.crawl(crawler, **spider_kwargs)

    # ── URL list parsing ──────────────────────────────────────────────────────

    def _collect_urls_from_lists_by_type(
        self, input_lists: List[str]
    ) -> tuple[List[str], List[str]]:
        """
        Parse weblists and split by prefix.
        sso- prefix  → SSO-protected URLs (AuthDownloaderMiddleware handles auth)
        no prefix    → standard link URLs
        git- prefix  → ignored here (GitManager's responsibility)
        """
        link_urls: List[str] = []
        sso_urls: List[str] = []

        for raw_url in self._collect_urls_from_lists(input_lists):
            if raw_url.startswith("git-"):
                continue          # GitManager owns these
            if raw_url.startswith("sso-"):
                sso_urls.append(raw_url.split("sso-", 1)[1])
            else:
                link_urls.append(raw_url)

        return link_urls, sso_urls

    def _collect_urls_from_lists(self, input_lists: List[str]) -> List[str]:
        urls: List[str] = []
        if not input_lists:
            return urls
        for list_name in input_lists:
            list_path = Path("weblists") / Path(list_name).name
            if not list_path.exists():
                logger.warning("Input list not found: %s", list_path)
                continue
            urls.extend(self._extract_urls_from_file(list_path))
        return urls

    def _extract_urls_from_file(self, path: Path) -> List[str]:
        urls: List[str] = []
        with path.open("r") as f:
            for line in f:
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                urls.append(stripped.split(",")[0].strip())
        return urls