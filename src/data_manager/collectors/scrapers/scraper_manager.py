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
        utils_config = load_utils_config()
        global_config = load_global_config()

        self.config = utils_config.get("scraper", {})
        self.sso_config = utils_config.get("sso", {})
        self.data_path = Path(global_config["DATA_PATH"])
        self.input_lists = (dm_config or {}).get("input_lists") or []

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
        websites_dir = persistence.websites_dir
        git_urls: List[str] = []
        sso_urls: List[str] = []

        if self.config.get("reset_data", False):
            persistence.reset_directory(websites_dir)

        for raw_url in self.collect_urls_from_lists():
            if raw_url.startswith("git-"):
                git_urls.append(raw_url.split("git-", 1)[1])
                continue

            if raw_url.startswith("sso-"):
                sso_urls.append(raw_url.split("sso-", 1)[1])
                continue

            self._handle_standard_url(raw_url, persistence)

        if sso_urls:
            sso_resources = self._collect_sso_resources(sso_urls)
            for resource in sso_resources:
                persistence.persist_scraped_resource(
                    resource, persistence.websites_dir
                )

        if git_urls:
            if self.config.get("reset_data", False):
                persistence.reset_directory(persistence.git_dir)
            git_resources = self._collect_git_resources(
                git_urls, persistence
            )
            logger.debug(f"Git scraping produced {len(git_resources)} resources")

        logger.debug("Web scraping was completed successfully")

    def collect_urls_from_lists(self) -> List[str]:
        """Collect URLs from the configured weblists."""
        urls: List[str] = []
        for list_name in self.input_lists:
            list_path = Path("weblists") / Path(list_name).name
            if not list_path.exists():
                logger.warning(f"Input list {list_path} not found.")
                continue

            urls.extend(self._extract_urls_from_file(list_path))

        return urls

    def _handle_standard_url(
        self, url: str, persistence: PersistenceService
    ) -> None:
        try:
            for resource in self.web_scraper.scrape(url):
                persistence.persist_scraped_resource(
                    resource, persistence.websites_dir
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
            persistence.persist_scraped_resource(resource, persistence.git_dir)
        return resources

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
