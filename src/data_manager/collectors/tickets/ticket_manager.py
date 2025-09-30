from typing import Iterable, Optional

from src.data_manager.collectors.persistence import PersistenceService
from src.data_manager.collectors.tickets.integrations.jira import JiraClient
from src.data_manager.collectors.tickets.integrations.redmine_tickets import (
    RedmineClient,
)
from src.data_manager.collectors.tickets.ticket_resource import TicketResource
from src.utils.config_loader import load_global_config
from src.utils.logging import get_logger

logger = get_logger(__name__)

global_config = load_global_config()

class TicketManager:
    """Coordinates ticket integrations and delegates persistence."""

    def __init__(self) -> None:
        self.data_path = global_config["DATA_PATH"]
        self.jira_client = JiraClient()
        self.redmine_client = RedmineClient()

    def collect(self, persistence: PersistenceService) -> None:
        self._persist_resources(self.jira_client.collect(), persistence)
        self._persist_resources(self.redmine_client.collect(), persistence)

    def run(self, persistence: Optional[PersistenceService] = None) -> None:
        """Backward-compatible entry point for legacy callers."""
        if persistence is None:
            persistence = PersistenceService(self.data_path)
        self.collect(persistence)
        persistence.flush_tickets()

    def _persist_resources(
        self,
        resources: Iterable[TicketResource] | None,
        persistence: PersistenceService,
    ) -> None:
        if not resources:
            return

        for resource in resources:
            try:
                persistence.persist_ticket(resource)
            except Exception as exc:
                logger.error(
                    f"Failed to persist ticket {resource.ticket_id} from {resource.source}: {exc}"
                )
