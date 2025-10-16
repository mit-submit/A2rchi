from typing import Any, Dict, Iterator, Optional

import jira

from src.data_manager.collectors.tickets.ticket_resource import TicketResource
from src.data_manager.collectors.utils.anonymizer import Anonymizer
from src.utils.env import read_secret
from src.utils.logging import get_logger

logger = get_logger(__name__)


class JiraClient:
    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        self.client: Optional[jira.JIRA] = None
        self.jira_url: Optional[str] = None
        self.jira_projects: list = []
        self.anonymize_data = True
        self.anonymizer: Optional[Anonymizer] = None
        self.visible: bool = True

        jira_config: Dict[str, Any] = dict(config or {})

        if not jira_config.get('enabled', False):
            logger.debug('JIRA source disabled; skipping data fetching from JIRA')
            return

        self.jira_config = jira_config
        self.jira_url = jira_config.get('url') or jira_config.get('JIRA_URL')
        projects = jira_config.get('projects') or jira_config.get('JIRA_PROJECTS')
        self.jira_projects = projects or []

        if not self.jira_url or not self.jira_projects:
            logger.info(
                "JIRA configs couldn't be found. A2rchi will skip data fetching from JIRA"
            )
            return

        try:
            pat = read_secret('JIRA_PAT')
        except FileNotFoundError as error:
            logger.warning(
                'JIRA Personal Access Token (PAT) not found. Skipping JIRA collection.',
                exc_info=error,
            )
            return

        self.anonymize_data = jira_config.get('anonymize_data', True)
        self.max_tickets = int(jira_config.get('max_tickets', 1e10))

        client = self.log_in(pat)
        if not client:
            logger.warning('Could not establish JIRA connection; skipping JIRA collection.')
            return

        self.client = client
        if self.anonymize_data:
            try:
                self.anonymizer = Anonymizer()
            except Exception as error:
                logger.warning('Failed to initialise JIRA anonymizer; continuing without anonymization.', exc_info=error)
                self.anonymize_data = False

    def log_in(self, pat: str) -> Optional[jira.JIRA]:
        try:
            return jira.JIRA(self.jira_url, token_auth=pat, timeout=30)
        except Exception as error:
            logger.error(f"Failed to log in to JIRA: {error}")
            return None

    def collect(self) -> Iterator[TicketResource]:
        """Return an iterator of tickets pulled from JIRA."""
        if not self.client or not self.jira_projects:
            logger.warning("Skipping JIRA collection; client not initialized or projects missing.")
            return iter(())

        return self._fetch_ticket_resources()

    def _fetch_ticket_resources(self) -> Iterator[TicketResource]:
        for issue in self.get_all_issues():
            issue_key = getattr(issue, "key", str(issue))
            created_at = getattr(issue.fields, "created", "")
            issue_text = self._build_issue_text(issue)

            content_parts = []
            if created_at:
                content_parts.append(created_at)
            content_parts.append(issue_text)
            content = "\n".join(part for part in content_parts if part)

            metadata = {
                "project": getattr(getattr(issue.fields, "project", None), "key", None),
                "url": f"{self.jira_url.rstrip('/')}/browse/{issue_key}" if self.jira_url else None,
            }

            record = TicketResource(
                ticket_id=str(issue_key),
                content=content,
                source_type="jira",
                created_at=created_at or None,
                metadata={k: v for k, v in metadata.items() if v},
            )

            logger.debug(f"Collected JIRA ticket {issue_key}")
            yield record

    def get_all_issues(self) -> Iterator[jira.Issue]:
        """Fetch all issues from the configured JIRA projects."""
        max_batch_results = 100  # You can adjust this up to 1000 for JIRA Cloud
        for project in self.jira_projects:
            logger.debug(f"Fetching issues for project: {project}")
            query = f"project={project}"
            start_at = 0
            while True:
                batch = self.client.search_issues(
                    query,
                    startAt=start_at,
                    maxResults=max_batch_results,
                )
                if not batch:
                    break
                yield from batch
                if len(batch) < max_batch_results:
                    break
                start_at += max_batch_results
                if start_at > self.max_tickets:
                    logger.warning(f"Reached max ticket limit of {self.max_tickets}. Stopping further fetch.")
                    break

    def _build_issue_text(self, issue: jira.Issue) -> str:
        """Return a formatted representation of a JIRA issue body."""
        summary = getattr(issue.fields, "summary", "")
        description = getattr(issue.fields, "description", "")

        issue_text = f"Title: {issue}\nSummary: {summary}\nDescription: {description}\n"

        comments = self.client.comments(issue)
        for comment in comments:
            body = getattr(comment, "body", "")
            issue_text += f"Comment: {body}\n"

        if self.anonymize_data and self.anonymizer:
            issue_text = self.anonymizer.anonymize(issue_text)

        return issue_text
