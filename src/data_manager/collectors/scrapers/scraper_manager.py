from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from src.data_manager.collectors.persistence import PersistenceService
from src.data_manager.collectors.scrapers.integrations.sso_scraper import \
    SSOCollector
from src.data_manager.collectors.scrapers.scraped_resource import \
    ScrapedResource
from src.data_manager.collectors.scrapers.scraper import WebScraper
from src.utils.config_loader import load_global_config, load_utils_config
from src.utils.logging import get_logger

logger = get_logger(__name__)

if TYPE_CHECKING:
    from src.data_manager.collectors.scrapers.integrations.git_scraper import \
        GitScraper


class ScraperManager:
    """Coordinates scraper integrations and centralises persistence logic."""

    def __init__(self, dm_config: Optional[Dict[str, Any]] = None) -> None:
        global_config = load_global_config()
        utils_config = load_utils_config()

        sources_config = (dm_config or {}).get("sources", {}) or {}
        links_config = sources_config.get("links", {}) if isinstance(sources_config, dict) else {}
        git_config = sources_config.get("git", {}) if isinstance(sources_config, dict) else {}
        sso_config = sources_config.get("sso", {}) if isinstance(sources_config, dict) else {}

        scraper_config = {}
        if isinstance(links_config, dict):
            scraper_config = links_config.get("scraper", {}) or {}
        if not scraper_config:
            scraper_config = utils_config.get("scraper", {}) or {}
        self.config = scraper_config

        self.links_enabled = links_config.get("enabled", True)
        self.git_enabled = git_config.get("enabled", False)
        self.sso_config = sso_config or {}
        self.sso_enabled = self.sso_config.get("enabled", False)
        self.data_path = Path(global_config["DATA_PATH"])
        self.input_lists = links_config.get("input_lists", [])
        self.git_dir = self.data_path / "git"

        self.data_path.mkdir(parents=True, exist_ok=True)

        self.web_scraper = WebScraper(
            verify_urls=self.config.get("verify_urls", True),
            enable_warnings=self.config.get("enable_warnings", True),
        )
        self._git_scraper: Optional["GitScraper"] = None
        self.sso_collector = SSOCollector(self.sso_config)

    def collect(
        self, persistence: PersistenceService
    ) -> None:
        """Run the configured scrapers and persist their output."""
        websites_dir = persistence.data_path / "websites"
        git_urls: List[str] = []
        sso_urls: List[str] = []

        if self.config.get("reset_data", False):
            persistence.reset_directory(websites_dir)

        if not self.links_enabled:
            logger.info("Links disabled, skipping all scraping")
            return

        for raw_url in self.collect_urls_from_lists():
            if raw_url.startswith("git-"):
                git_urls.append(raw_url.split("git-", 1)[1])
                continue

            if raw_url.startswith("sso-"):
                sso_urls.append(raw_url.split("sso-", 1)[1])
                continue

            if self.links_enabled:
                self._handle_standard_url(raw_url, persistence, websites_dir)

        if self.sso_enabled and sso_urls:
            sso_resources = self._collect_sso_resources(sso_urls)
            for resource in sso_resources:
                persistence.persist_resource(
                    resource, websites_dir
                )
        elif sso_urls:
            logger.warning("SSO URLs detected but SSO source is disabled; skipping SSO scraping")

        if self.git_enabled and git_urls:
            if self.config.get("reset_data", False):
                persistence.reset_directory(self.git_dir)
            git_resources = self._collect_git_resources(
                git_urls, persistence
            )
            logger.debug(f"Git scraping produced {len(git_resources)} resources")
        elif git_urls:
            logger.warning("Git URLs detected but git source is disabled; skipping git scraping")

        logger.info("Web scraping was completed successfully")

    def collect_urls_from_lists(self) -> List[str]:
        """Collect URLs from the configured weblists."""
        urls: List[str] = []
        # Handle case where input_lists might be None
        if not self.input_lists:
            return urls
        for list_name in self.input_lists:
            list_path = Path("weblists") / Path(list_name).name
            if not list_path.exists():
                logger.warning(f"Input list {list_path} not found.")
                continue

            urls.extend(self._extract_urls_from_file(list_path))

        return urls

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
    ) -> List[ScrapedResource]:
        git_scraper = self._get_git_scraper()
        resources = git_scraper.collect(git_urls)
        for resource in resources:
            persistence.persist_resource(resource, self.git_dir)
        return resources

    # ------------------------------------------------------------------
    # Backwards compatibility helpers for manual uploader workflows
    # ------------------------------------------------------------------
    def register_resource(self, target_dir: Path, resource: ScrapedResource) -> Path:
        """Persist a scraped resource using a fresh persistence service."""
        persistence = PersistenceService(self.data_path)
        path = persistence.persist_resource(resource, target_dir)
        persistence.flush_index()
        return path

    def persist_sources(self) -> None:
        """Flush the unified index when running outside the main pipeline."""
        persistence = PersistenceService(self.data_path)
        persistence.flush_index()

    def _get_git_scraper(self) -> "GitScraper":
        if self._git_scraper is None:
            from src.data_manager.collectors.scrapers.integrations.git_scraper import \
                GitScraper

            self._git_scraper = GitScraper(manager=self)
        return self._git_scraper

    def _collect_sso_resources(self, urls: List[str]) -> List[ScrapedResource]:
        resources: List[ScrapedResource] = []
        for url in urls:
            resources.extend(self.sso_collector.collect(url))
        return resources
