import importlib
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from src.data_manager.collectors.persistence import PersistenceService
from src.data_manager.collectors.scrapers.scraped_resource import \
    ScrapedResource
from src.data_manager.collectors.scrapers.scraper import WebScraper
from src.utils.config_loader import load_global_config, load_utils_config
from src.utils.logging import get_logger

logger = get_logger(__name__)

if TYPE_CHECKING:
    from src.data_manager.collectors.scrapers.integrations.git_scraper import (
        GitScraper,
    )


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

    def collect(
        self, persistence: PersistenceService
    ) -> None:
        """Run the configured scrapers and persist their output."""
        websites_dir = persistence.websites_dir
        git_urls: List[str] = []

        if self.config.get("reset_data", False):
            persistence.reset_directory(websites_dir)

        for raw_url in self.collect_urls_from_lists():
            if raw_url.startswith("git-"):
                git_urls.append(raw_url.split("git-", 1)[1])
                continue

            if raw_url.startswith("sso-"):
                url = raw_url.split("sso-", 1)[1]
                self._handle_sso_url(url, persistence)
                continue

            self._handle_standard_url(raw_url, persistence)

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

    def _handle_sso_url(self, url: str, persistence: PersistenceService) -> None:
        if not self.sso_config or not self.sso_config.get("enabled", False):
            logger.error("SSO is disabled or not configured")
            return

        sso_class_name = self.sso_config.get("sso_class", "")
        sso_class_map = self.sso_config.get("sso_class_map", {})
        sso_entry = sso_class_map.get(sso_class_name)
        if not sso_entry:
            logger.error(f"SSO class {sso_class_name} not configured")
            return

        sso_class = sso_entry.get("class")
        if isinstance(sso_class, str):
            module = importlib.import_module("src.data_manager.collectors.scrapers.integrations.sso_scraper")
            sso_class = getattr(module, sso_class)

        sso_kwargs = sso_entry.get("kwargs", {})

        try:
            with sso_class(**sso_kwargs) as sso_scraper:
                crawled_payload = sso_scraper.crawl(url)
                resources = self._extract_sso_resources(sso_scraper, crawled_payload)
                if not resources:
                    logger.warning(f"No content extracted from SSO crawl for {url}")
                    return

                for resource in resources:
                    persistence.persist_scraped_resource(
                        resource, persistence.websites_dir
                    )
        except Exception as exc:
            logger.error(f"SSO scraping failed for {url}: {exc}")

    def _extract_sso_resources(self, sso_scraper, crawled_payload) -> List[ScrapedResource]:
        resources: List[ScrapedResource] = []

        page_data = getattr(sso_scraper, "page_data", None)
        if isinstance(page_data, list):
            for page in page_data:
                if not isinstance(page, dict):
                    continue
                page_url = page.get("url")
                content = page.get("content")
                if not page_url or content is None:
                    continue

                resources.append(
                    ScrapedResource(
                        url=page_url,
                        content=content,
                        suffix=page.get("suffix", "html"),
                        metadata={"title": page.get("title"), "source": "sso"},
                    )
                )

        elif isinstance(crawled_payload, list):
            for item in crawled_payload:
                if not isinstance(item, dict):
                    continue
                page_url = item.get("url")
                content = item.get("content")
                if not page_url or content is None:
                    continue
                resources.append(
                    ScrapedResource(
                        url=page_url,
                        content=content,
                        suffix=item.get("suffix", "html"),
                        metadata={"source": "sso"},
                    )
                )

        elif isinstance(crawled_payload, dict):
            for page_url in crawled_payload.values():
                logger.warning(
                    f"SSO scraper returned mapping without page content; skipping {page_url}"
                )

        else:
            if crawled_payload is not None:
                logger.warning(
                    f"Unsupported SSO payload type {type(crawled_payload).__name__}"
                )

        return resources

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
            from src.data_manager.collectors.scrapers.integrations.git_scraper import (
                GitScraper,
            )

            self._git_scraper = GitScraper(manager=self)
        return self._git_scraper
