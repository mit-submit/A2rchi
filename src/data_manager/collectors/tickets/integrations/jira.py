from typing import Iterator, Optional

import jira

from src.data_manager.collectors.tickets.ticket_resource import TicketResource
from src.data_manager.collectors.utils.anonymizer import Anonymizer
from src.utils.config_loader import load_utils_config
from src.utils.env import read_secret
from src.utils.logging import get_logger

logger = get_logger(__name__)


class JiraClient:
    def __init__(self) -> None:
        try:
            self.jira_config = load_utils_config()["jira"]
            self.jira_url = self.jira_config["url"]
            self.jira_projects = self.jira_config["projects"]

            if not self.jira_url or not self.jira_projects:
                logger.info(
                    "JIRA configs couldn't be found. A2rchi will skip data fetching from JIRA"
                )
                self.client = None
                return
        except KeyError as error:
            raise KeyError(
                "JIRA configs couldn't be found. A2rchi will skip data fetching from JIRA: "
                f"{str(error)}"
            )

        try:
            self.pat = read_secret("JIRA_PAT")
        except FileNotFoundError as error:
            raise FileNotFoundError(
                "JIRA Personal Access Token (PAT) not found. Please set it up in your environment."
            ) from error

        try:
            self.anonymize_data = self.jira_config.get("anonymize_data", True)

            self.client = self.log_in(self.pat)
            self.anonymizer = Anonymizer()

            if not self.client:
                raise RuntimeError("No JIRA connection")
        except Exception as error:
            raise Exception(f"Error initializing JiraReader\n{str(error)}") from error

    def log_in(self, pat: str) -> Optional[jira.JIRA]:
        try:
            return jira.JIRA(self.jira_url, token_auth=pat, timeout=30)
        except Exception as error:
            logger.error(f"Failed to log in to JIRA: {error}")
            return None

    def collect(self) -> Iterator[TicketResource]:
        """Return an iterator of tickets pulled from JIRA."""
        if not self.client or not self.jira_projects:
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
                "url": f"{self.jira_url}/browse/{issue_key}" if self.jira_url else None,
            }

            record = TicketResource(
                ticket_id=str(issue_key),
                content=content,
                source="jira",
                created_at=created_at or None,
                metadata={k: v for k, v in metadata.items() if v},
            )

            logger.debug(f"Collected JIRA ticket {issue_key}")
            yield record

    def get_all_issues(self) -> Iterator[jira.Issue]:
        """Fetch all issues from the configured JIRA projects."""
        max_results = 100  # You can adjust this up to 1000 for JIRA Cloud
        for project in self.jira_projects:
            logger.debug(f"Fetching issues for project: {project}")
            query = f"project={project}"
            start_at = 0
            while True:
                batch = self.client.search_issues(
                    query,
                    startAt=start_at,
                    maxResults=max_results,
                )
                if not batch:
                    break
                yield from batch
                if len(batch) < max_results:
                    break
                start_at += max_results

    def _build_issue_text(self, issue: jira.Issue) -> str:
        """Return a formatted representation of a JIRA issue body."""
        summary = getattr(issue.fields, "summary", "")
        description = getattr(issue.fields, "description", "")

        issue_text = f"Title: {issue}\nSummary: {summary}\nDescription: {description}\n"

        comments = self.client.comments(issue)
        for comment in comments:
            body = getattr(comment, "body", "")
            issue_text += f"Comment: {body}\n"

        if self.anonymize_data:
            issue_text = self.anonymizer.anonymize(issue_text)

        return issue_text
