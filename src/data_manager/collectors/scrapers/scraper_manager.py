import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from src.data_manager.collectors.persistence import PersistenceService
from src.data_manager.collectors.scrapers.integrations.sso_scraper import \
    SSOCollector
from src.data_manager.collectors.scrapers.scraped_resource import \
    ScrapedResource
from src.data_manager.collectors.scrapers.scraper import WebScraper
from src.utils.config_loader import load_global_config
from src.utils.logging import get_logger

logger = get_logger(__name__)

if TYPE_CHECKING:
    from src.data_manager.collectors.scrapers.integrations.git_scraper import \
        GitScraper


class ScraperManager:
    """Coordinates scraper integrations and centralises persistence logic."""

    def __init__(self, dm_config: Optional[Dict[str, Any]] = None) -> None:
        global_config = load_global_config()

        sources_config = (dm_config or {}).get("sources", {}) or {}
        links_config = sources_config.get("links", {}) if isinstance(sources_config, dict) else {}
        git_config = sources_config.get("git", {}) if isinstance(sources_config, dict) else {}
        sso_config = sources_config.get("sso", {}) if isinstance(sources_config, dict) else {}

        scraper_config = {}
        if isinstance(links_config, dict):
            scraper_config = links_config.get("scraper", {}) or {}
        self.config = scraper_config

        self.links_enabled = links_config.get("enabled", True)
        self.git_enabled = git_config.get("enabled", True) if isinstance(git_config, dict) else True
        self.sso_enabled = sso_config.get("enabled", True)
        self.git_config = git_config if isinstance(git_config, dict) else {}

        self.web_scraper = WebScraper(
            verify_urls=self.config.get("verify_urls", True),
            enable_warnings=self.config.get("enable_warnings", True),
        )
        self._git_scraper: Optional["GitScraper"] = None
        self.sso_collector = SSOCollector(sso_config)

    def collect_all_from_config(
        self, persistence: PersistenceService
    ) -> None:
        """Run the configured scrapers and persist their output."""
        input_lists = self.config.get("input_lists", [])
        link_urls, git_urls, sso_urls = self._collect_urls_from_lists_by_type(input_lists)

        self.collect_links(persistence, link_urls=link_urls)
        self.collect_sso(persistence, sso_urls=sso_urls)
        self.collect_git(persistence, git_urls=git_urls)

        logger.info("Web scraping was completed successfully")

    def collect_links(
        self,
        persistence: PersistenceService,
        link_urls: List[str] = [],
    ) -> None:
        """Collect only standard link sources."""
        if not self.links_enabled:
            logger.info("Links disabled, skipping link scraping")
            return
        if not link_urls:
            return
        websites_dir = persistence.data_path / "websites"
        if not os.path.exists(websites_dir):
            os.makedirs(websites_dir, exist_ok=True)
        self._collect_links_from_urls(link_urls, persistence, websites_dir)

    def collect_git(
        self,
        persistence: PersistenceService,
        git_urls: Optional[List[str]] = None,
    ) -> None:
        """Collect only git sources."""
        if not self.git_enabled:
            logger.info("Git disabled, skipping git scraping")
            return
        if not git_urls:
            return
        git_dir = persistence.data_path / "git"
        if not os.path.exists(git_dir):
            os.makedirs(git_dir, exist_ok=True)
        self._collect_git_resources(git_urls, persistence, git_dir)

    def collect_sso(
        self,
        persistence: PersistenceService,
        sso_urls: Optional[List[str]] = None,
    ) -> None:
        """Collect only SSO sources."""
        if not self.sso_enabled:
            logger.info("SSO disabled, skipping SSO scraping")
            return
        if not sso_urls:
            return
        sso_dir = persistence.data_path / "sso"
        if not os.path.exists(sso_dir):
            os.makedirs(sso_dir, exist_ok=True)
        self._collect_sso_from_urls(sso_urls, persistence, sso_dir)

    def schedule_collect_links(self, persistence: PersistenceService, last_run: Optional[str] = None) -> None:
        """
        Scheduled collection of link sources.
        For now, this behaves the same as a full collection, overriding last_run depending on the persistence layer.
        """
        metadata = self.persistence.catalog.get_metadata_by_filter("source_type", source_type="links", metadata_keys=["url"])
        catalog_urls = [m[1].get("url", "") for m in metadata]
        self.collect_links(persistence, link_urls=catalog_urls)

    def schedule_collect_git(self, persistence: PersistenceService, last_run: Optional[str] = None) -> None:
        metadata = self.persistence.catalog.get_metadata_by_filter("source_type", source_type="git", metadata_keys=["url"])
        catalog_urls = [m[1].get("url", "") for m in metadata]
        self.collect_git(persistence, git_urls=catalog_urls)

    def schedule_collect_sso(self, persistence: PersistenceService, last_run: Optional[str] = None) -> None:
        metadata = self.persistence.catalog.get_metadata_by_filter("source_type", source_type="sso", metadata_keys=["url"])
        catalog_urls = [m[1].get("url", "") for m in metadata]
        self.collect_sso(persistence, sso_urls=catalog_urls)

    def _collect_links_from_urls(
        self,
        urls: List[str],
        persistence: PersistenceService,
        websites_dir: Path,
    ) -> None:
        for url in urls:
            self._handle_standard_url(url, persistence, websites_dir)

    def _collect_sso_from_urls(
        self,
        urls: List[str],
        persistence: PersistenceService,
        websites_dir: Path,
    ) -> None:
        if not urls:
            return
        sso_resources = self._collect_sso_resources(urls)
        for resource in sso_resources:
            persistence.persist_resource(resource, websites_dir)

    def _collect_sso_resources(self, urls: List[str]) -> List[ScrapedResource]:
        resources: List[ScrapedResource] = []
        for url in urls:
            resources.extend(self.sso_collector.collect(url))
        return resources

    def _collect_urls_from_lists(self, input_lists: List[str]) -> List[str]:
        """Collect URLs from the configured weblists."""
        urls: List[str] = []
        # Handle case where input_lists might be None
        if not input_lists:
            return urls
        for list_name in input_lists:
            list_path = Path("weblists") / Path(list_name).name
            if not list_path.exists():
                logger.warning(f"Input list {list_path} not found.")
                continue

            urls.extend(self._extract_urls_from_file(list_path))

        return urls

    def _collect_urls_from_lists_by_type(self, input_lists: List[str]) -> tuple[List[str], List[str], List[str]]:
        """All types of URLs are in the same input lists, separate them via prefixes"""
        link_urls: List[str] = []
        git_urls: List[str] = []
        sso_urls: List[str] = []
        for raw_url in self._collect_urls_from_lists(input_lists):
            if raw_url.startswith("git-"):
                git_urls.append(raw_url.split("git-", 1)[1])
                continue
            if raw_url.startswith("sso-"):
                sso_urls.append(raw_url.split("sso-", 1)[1])
                continue
            link_urls.append(raw_url)
        return link_urls, git_urls, sso_urls

    def _handle_standard_url(
        self, url: str, persistence: PersistenceService, websites_dir: Path
    ) -> None:
        try:
            for resource in self.web_scraper.scrape(url):
                persistence.persist_resource(
                    resource, websites_dir
                )
        except Exception as exc:
            logger.error(f"Failed to scrape {url}: {exc}")

    def _extract_urls_from_file(self, path: Path) -> List[str]:
        urls: List[str] = []
        with path.open("r") as file:
            for line in file:
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                urls.append(stripped)
        return urls

    def _collect_git_resources(
        self,
        git_urls: List[str],
        persistence: PersistenceService,
        git_dir: Path,
    ) -> List[ScrapedResource]:
        git_scraper = self._get_git_scraper()
        resources = git_scraper.collect(git_urls)
        for resource in resources:
            persistence.persist_resource(resource, git_dir)
        return resources

    def _get_git_scraper(self) -> "GitScraper":
        if self._git_scraper is None:
            from src.data_manager.collectors.scrapers.integrations.git_scraper import \
                GitScraper

            self._git_scraper = GitScraper(manager=self, git_config=self.git_config)
        return self._git_scraper
