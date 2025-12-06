from typing import Any, Dict, Iterable, Optional
from datetime import datetime, date

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

        self.jira_config = dict(sources_config.get('jira', {}))
        self.redmine_config = dict(sources_config.get('redmine', {}))

        self.last_collected_at = {}
        self.cutoff_dates = {'JIRA':None,'Redmine':None}

        self.jira_client = None
        if self.jira_config.get('enabled', False):
            self.jira_client = self._init_client(lambda: JiraClient(self.jira_config), "JIRA")
            try:
                self.cutoff_dates['JIRA'] = self.jira_config.get('cutoff_date') if isinstance(self.jira_config.get('cutoff_date'),date) else datetime.strptime(self.jira_config.get('cutoff_date').strip(),'%Y-%m-%d')
            except Exception as e:
                logger.warning(str(e))
                logger.warning(f"The JIRA cutoff date {self.jira_config.get('cutoff_date')} is not in YYYY-MM-DD format. Skipping attribute.")


        self.redmine_client = None
        if self.redmine_config.get('enabled', False):
            self.redmine_client = self._init_client(lambda: RedmineClient(self.redmine_config), "Redmine")
            try:
                self.cutoff_dates['Redmine'] = self.redmine_config.get('cutoff_date') if isinstance(self.redmine_config.get('cutoff_date'),date) else datetime.strptime(self.redmine_config.get('cutoff_date').strip(),'%Y-%m-%d')
            except Exception as e:
                logger.warning(str(e))
                logger.warning(f"The Redmine cutoff date {self.redmine_config.get('cutoff_date')} is not in YYYY-MM-DD format. Skipping attribute.")

    def collect(self, persistence: PersistenceService) -> None:            
        self._collect_from_client(self.jira_client, "JIRA", persistence, None,self.cutoff_dates['JIRA'])
        self._collect_from_client(self.redmine_client, "Redmine", persistence, None, self.cutoff_dates['Redmine'])

    def update_tickets(self, persistence: PersistenceService) -> None:
        now = datetime.now()

        if self.jira_config.get('enabled', False):
            jira_frequency = self.jira_config.get('frequency')
            date_last_collected_at = self.last_collected_at["JIRA"]
            cutoff_date = self.cutoff_dates['JIRA']
            if (date_last_collected_at-now).days>=jira_frequency or jira_frequency==0:
                self._collect_from_client(self.jira_client, "JIRA", persistence, date_last_collected_at, cutoff_date)

        if self.redmine_config.get('enabled', False):
            redmine_frequency = self.redmine_config.get('frequency')
            date_last_collected_at = self.last_collected_at["Redmine"]
            cutoff_date = self.cutoff_dates['Redmine']
            if (date_last_collected_at-now).days>=redmine_frequency or redmine_frequency==0:
                self._collect_from_client(self.redmine_client, "Redmine", persistence, date_last_collected_at, cutoff_date)



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
        collect_since: datetime,
        cutoff_date: datetime
    ) -> None:
        if client is None:
            return

        try:
            resources = client.collect(collect_since, cutoff_date)
        except Exception as exc:
            logger.warning(
                f"{name} collection failed; skipping remaining tickets from this source.",
                exc_info=exc,
            )
            return

        self._persist_resources(resources, persistence)

        self.last_collected_at[name] = datetime.now()

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
