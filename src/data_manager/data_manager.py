import os

from src.data_manager.collectors.persistence import PersistenceService
from src.data_manager.collectors.scrapers.scraper_manager import ScraperManager
from src.data_manager.collectors.tickets.ticket_manager import TicketManager
from src.data_manager.vectorstore.manager import VectorStoreManager
from src.utils.config_loader import load_config
from src.utils.logging import get_logger

logger = get_logger(__name__)

class DataManager():

    def __init__(self, *, run_ingestion: bool = True):

        self.config = load_config(map=True)
        self.global_config = self.config["global"]
        self.data_path = self.global_config["DATA_PATH"]
        self.run_ingestion = run_ingestion

        os.makedirs(self.data_path, exist_ok=True)

        self.persistence = PersistenceService(self.data_path)

        self.scraper_manager = ScraperManager(dm_config=self.config["data_manager"])
        self.ticket_manager = TicketManager(dm_config=self.config["data_manager"])

        self.vector_manager = VectorStoreManager(
            config=self.config,
            global_config=self.global_config,
            data_path=self.data_path,
        )

        self.collection_name = self.vector_manager.collection_name
        self.distance_metric = self.vector_manager.distance_metric
        self.embedding_model = self.vector_manager.embedding_model
        self.text_splitter = self.vector_manager.text_splitter
        self.stemmer = self.vector_manager.stemmer

        logger.info(f"Using collection: {self.collection_name}")

        if self.run_ingestion:
            self._run_initial_ingestion()

    def _run_initial_ingestion(self) -> None:
        source_aggregation = [
            (
                "Scraping documents onto filesystem",
                lambda: self.scraper_manager.collect(self.persistence),
            ),
            (
                "Fetching ticket data onto filesystem",
                lambda: self.ticket_manager.collect(self.persistence),
            ),
        ]

        for message, step in source_aggregation:
            logger.info(message)
            step()

        self.persistence.flush_index()

        self.vector_manager.delete_existing_collection_if_reset()
        self.vector_manager.update_vectorstore()

    def delete_existing_collection_if_reset(self, *, force: bool = False):
        """Proxy to the underlying vector manager."""
        if not (self.run_ingestion or force):
            logger.debug("Skipping collection reset check (ingestion disabled).")
            return None
        return self.vector_manager.delete_existing_collection_if_reset()

    def fetch_collection(self):
        """Proxy to the underlying vector manager."""
        return self.vector_manager.fetch_collection()

    def update_vectorstore(self, *, force: bool = False):
        """Proxy to the underlying vector manager."""
        if not (self.run_ingestion or force):
            logger.debug("Skipping vectorstore update (ingestion disabled).")
            return None
        self.vector_manager.update_vectorstore()

    def _update_after_collect(self) -> None:
        self.persistence.flush_index()
        self.vector_manager.update_vectorstore()

    def collect_links(self) -> None:
        logger.info("Collecting link sources")
        self.scraper_manager.collect_links(self.persistence)
        self._update_after_collect()

    def collect_git(self) -> None:
        logger.info("Collecting git sources")
        self.scraper_manager.collect_git(self.persistence)
        self._update_after_collect()

    def collect_sso(self) -> None:
        logger.info("Collecting SSO sources")
        self.scraper_manager.collect_sso(self.persistence)
        self._update_after_collect()

    def collect_jira(self) -> None:
        logger.info("Collecting JIRA tickets")
        self.ticket_manager.collect_jira(self.persistence)
        self._update_after_collect()

    def collect_redmine(self) -> None:
        logger.info("Collecting Redmine tickets")
        self.ticket_manager.collect_redmine(self.persistence)
        self._update_after_collect()
