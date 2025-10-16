from typing import Any, Dict, Iterable, Optional

from src.data_manager.collectors.persistence import PersistenceService
from src.data_manager.collectors.tickets.integrations.jira import JiraClient
from src.data_manager.collectors.tickets.integrations.redmine_tickets import \
    RedmineClient
from src.data_manager.collectors.tickets.ticket_resource import TicketResource
from src.utils.config_loader import load_global_config
from src.utils.logging import get_logger

logger = get_logger(__name__)

global_config = load_global_config()

class TicketManager:
    """Coordinates ticket integrations and delegates persistence."""

    def __init__(self, dm_config: Optional[Dict[str, Any]] = None) -> None:
        self.data_path = global_config["DATA_PATH"]
        raw_sources = (dm_config or {}).get('sources', {}) if isinstance(dm_config, dict) else {}
        sources_config = dict(raw_sources) if isinstance(raw_sources, dict) else {}

        jira_config = dict(sources_config.get('jira', {}))
        redmine_config = dict(sources_config.get('redmine', {}))

        self.jira_client = None
        if jira_config.get('enabled', False):
            self.jira_client = self._init_client(lambda: JiraClient(jira_config), "JIRA")

        self.redmine_client = None
        if redmine_config.get('enabled', False):
            self.redmine_client = self._init_client(lambda: RedmineClient(redmine_config), "Redmine")

    def collect(self, persistence: PersistenceService) -> None:
        self._collect_from_client(self.jira_client, "JIRA", persistence)
        self._collect_from_client(self.redmine_client, "Redmine", persistence)

    def run(self, persistence: Optional[PersistenceService] = None) -> None:
        """Backward-compatible entry point for legacy callers."""
        if persistence is None:
            persistence = PersistenceService(self.data_path)
        self.collect(persistence)
        persistence.flush_index()

    def _init_client(self, factory, name: str):
        try:
            return factory()
        except Exception as exc:
            logger.warning(
                f"{name} client unavailable; skipping ticket collection.",
                exc_info=exc,
            )
            return None

    def _collect_from_client(
        self,
        client,
        name: str,
        persistence: PersistenceService,
    ) -> None:
        if client is None:
            return

        try:
            resources = client.collect()
        except Exception as exc:
            logger.warning(
                f"{name} collection failed; skipping remaining tickets from this source.",
                exc_info=exc,
            )
            return

        self._persist_resources(resources, persistence)

    def _persist_resources(
        self,
        resources: Iterable[TicketResource] | None,
        persistence: PersistenceService,
    ) -> None:
        if not resources:
            return

        for resource in resources:
            try:
                persistence.persist_resource(resource, persistence.data_path / "tickets")
            except Exception as exc:
                logger.error(
                    f"Failed to persist ticket {resource.ticket_id} from {resource.source}: {exc}"
                )
