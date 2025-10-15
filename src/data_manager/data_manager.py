import os

from src.data_manager.collectors.persistence import PersistenceService
from src.data_manager.collectors.scrapers.scraper_manager import ScraperManager
from src.data_manager.collectors.tickets.ticket_manager import TicketManager
from src.data_manager.vectorstore.manager import VectorStoreManager
from src.utils.config_loader import load_config
from src.utils.logging import get_logger

logger = get_logger(__name__)

class DataManager():

    def __init__(self):

        self.config = load_config(map=True)
        self.global_config = self.config["global"]
        self.data_path = self.global_config["DATA_PATH"]

        os.makedirs(self.data_path, exist_ok=True)

        self.persistence = PersistenceService(self.data_path)

        scraper_manager = ScraperManager(dm_config=self.config["data_manager"])
        ticket_manager = TicketManager(dm_config=self.config["data_manager"])

        source_aggregation = [
            (
                "Scraping documents onto filesystem",
                lambda: scraper_manager.collect(self.persistence),
            ),
            (
                "Fetching ticket data onto filesystem",
                lambda: ticket_manager.collect(self.persistence),
            ),
        ]

        for message, step in source_aggregation:
            logger.info(message)
            step()

        self.persistence.flush_index()

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

        self.vector_manager.delete_existing_collection_if_reset()
        self.vector_manager.update_vectorstore()

    def delete_existing_collection_if_reset(self):
        """Proxy to the underlying vector manager."""
        return self.vector_manager.delete_existing_collection_if_reset()

    def fetch_collection(self):
        """Proxy to the underlying vector manager."""
        return self.vector_manager.fetch_collection()

    def update_vectorstore(self):
        """Proxy to the underlying vector manager."""
        self.vector_manager.update_vectorstore()
