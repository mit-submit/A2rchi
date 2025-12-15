from dateutil.parser import parse
from threading import Lock
from typing import Any, Dict, Iterator, Optional
from datetime import datetime, timezone
from time import perf_counter

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

        self.anonymize_data = jira_config.get('anonymize_data', False)
        self.max_tickets = int(jira_config.get('max_tickets', 1e10))

        client = self.log_in(pat)
        if not client:
            logger.warning('Could not establish JIRA connection; skipping JIRA collection.')
            return

        self.client = client
        self.jira_timezone = None
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
        
    def _get_jira_timezone(self) -> Optional[timezone]:
        """Infer JIRA server timezone from a test query."""
        if not self.client or not self.jira_projects:
            return None
        try:
            issues = self.client.search_issues(
                f"project={self.jira_projects[0]} ORDER BY updated DESC",
                maxResults=1, fields=["updated"]
            )
            if issues and (dt := self._parse_date(getattr(issues[0].fields, "updated", ""))):
                if dt.tzinfo and (offset := dt.utcoffset()):
                    return timezone(offset)
        except Exception:
            pass
        return None

    def collect(self, collect_since: datetime, cutoff_date: datetime) -> Iterator[TicketResource]:
        """Return an iterator of tickets pulled from JIRA."""
        if not self.client or not self.jira_projects:
            logger.warning("Skipping JIRA collection; client not initialized or projects missing.")
            return iter(())

        return self._fetch_ticket_resources(collect_since, cutoff_date)

    def _fetch_ticket_resources(self, collect_since: datetime, cutoff_date: datetime) -> Iterator[TicketResource]:
        trimmed_url = self.jira_url.rstrip('/') if self.jira_url else None
        for issue in self.get_all_issues(collect_since, cutoff_date):
            fields = getattr(issue, "fields", None)
            issue_key = getattr(issue, "key", str(issue))
            created_at = getattr(fields, "created", "") if fields else ""
            created_at = self._parse_date(created_at) if created_at!="" else ""
            updated_at = getattr(fields, "updated", "") if fields else ""
            updated_at = self._parse_date(updated_at) if updated_at!="" else ""
            issue_text = self._build_issue_text(issue, fields, cutoff_date)

            content_parts = []
            if created_at:
                content_parts.append(created_at.strftime('%Y-%m-%dT%H:%M:%S.%f%z'))
            if updated_at:
                content_parts.append(created_at.strftime('%Y-%m-%dT%H:%M:%S.%f%z'))
            content_parts.append(issue_text)
            content = "\n".join(part for part in content_parts if part)

            metadata = {
                "project": getattr(getattr(fields, "project", None), "key", None) if fields else None,
                "url": f"{trimmed_url}/browse/{issue_key}" if trimmed_url else None,
            }

            record = TicketResource(
                ticket_id=str(issue_key),
                content=content,
                source_type="jira",
                created_at=created_at or None,
                metadata={k: v for k, v in metadata.items() if v},
            )
            
            if collect_since:
                logger.debug(
                    "Fetched recently updated/created ticket | ticket=%s collect_since=%s created=%s updated=%s",
                    issue_key,
                    collect_since.strftime('%Y-%m-%d %H:%M:%S'),
                    created_at or 'N/A',
                    updated_at or 'N/A',
                )
            else:
                logger.debug(f"Collected JIRA ticket {issue_key}")
            yield record

    def _format_date_for_jql(self, dt: datetime) -> str:
        """Convert UTC datetime to JIRA server timezone and format for JQL."""
        if not dt:
            return ""
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        elif dt.tzinfo != timezone.utc:
            dt = dt.astimezone(timezone.utc)
        if not self.jira_timezone:
            self.jira_timezone = self._get_jira_timezone()
        if self.jira_timezone:
            dt = dt.astimezone(self.jira_timezone)
        return dt.strftime("%Y-%m-%d %H:%M")

    def get_all_issues(self, collect_since: datetime, cutoff_date: datetime) -> Iterator[jira.Issue]:
        """Fetch all issues from the configured JIRA projects."""
        max_batch_results = min(100, self.max_tickets)  # You can adjust this up to 1000 for JIRA Cloud
        for project in self.jira_projects:
            logger.debug(f"Fetching maximum of {int(self.max_tickets)} issues in batches of {max_batch_results} for project: {project}")
            query = f"project={project}"
            if collect_since:
                jql_date_string = self._format_date_for_jql(collect_since)
                query += f' AND (updated > "{jql_date_string}" OR created > "{jql_date_string}")'

            if cutoff_date:
                jql_date_string = self._format_date_for_jql(cutoff_date)
                query += f' AND created < "{jql_date_string}"'

            logger.debug(query)
            start_at = 0
            project_start = perf_counter()
            while True:
                fetch_start = perf_counter()
                batch = self.client.search_issues(
                    query,
                    startAt=start_at,
                    maxResults=max_batch_results,
                    fields=["summary", "description", "project", "created","updated", "comment"],
                    expand="renderedFields,comment",
                )
                fetch_duration = perf_counter() - fetch_start
                if not batch:
                    logger.info(
                        "JIRA search returned 0 issues | project=%s startAt=%d duration=%.2fs",
                        project,
                        start_at,
                        fetch_duration,
                    )
                    break
                logger.info(
                    "Fetched %d JIRA issues | project=%s startAt=%d duration=%.2fs",
                    len(batch),
                    project,
                    start_at,
                    fetch_duration,
                )
                yield from batch
                if len(batch) < max_batch_results:
                    break
                start_at += max_batch_results
                if start_at > self.max_tickets:
                    logger.warning(f"Reached max ticket limit of {self.max_tickets}. Stopping further fetch.")
                    break
            project_duration = perf_counter() - project_start
            logger.info("Completed JIRA fetch for project=%s in %.2fs", project, project_duration)

    def _build_issue_text(self, issue: jira.Issue, fields: Optional[Any], cutoff_date: datetime) -> str:
        """Return a formatted representation of a JIRA issue body."""
        issue_key = getattr(issue, "key", str(issue))
        build_start = perf_counter()

        summary = getattr(fields, "summary", "") if fields else ""
        description = getattr(fields, "description", "") if fields else ""

        issue_text = f"Title: {issue}\nSummary: {summary}\nDescription: {description}\n"

        comments_field = getattr(fields, "comment", None) if fields else None
        comments = getattr(comments_field, "comments", []) if comments_field else []
        comments_start = perf_counter()
        for comment in comments:
            if cutoff_date:
                comment_created_dt = parse(comment.created)
                if comment_created_dt.date() < cutoff_date:
                    body = getattr(comment, "body", "")
                    if body:
                        issue_text += f"Comment: {body}\n"
            else:
                body = getattr(comment, "body", "")
                if body:
                    issue_text += f"Comment: {body}\n"

        comments_duration = perf_counter() - comments_start

        anonymize_duration = 0.0
        if self.anonymize_data and self.anonymizer:
            anonymize_start = perf_counter()
            issue_text = self.anonymizer.anonymize(issue_text)
            anonymize_duration = perf_counter() - anonymize_start

        total_duration = perf_counter() - build_start
        logger.debug(
            "Built issue text for %s | comments=%.3fs anonymize=%.3fs total=%.3fs",
            issue_key,
            comments_duration,
            anonymize_duration,
            total_duration,
        )

        return issue_text

    def _parse_date(self,date_string: str) -> datetime:
        """Return a date object from the JIRA date extracted string."""

        try:
            dt_object = datetime.strptime(date_string, '%Y-%m-%dT%H:%M:%S.%f%z')
        except:
            logger.warning("Error parsing date from JIRA. Reverting to ignoring time zone.")
            dt_object = datetime.strptime(date_string[:20], '%Y-%m-%dT%H:%M:%S')

        return dt_object
