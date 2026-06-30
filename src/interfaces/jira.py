from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Optional

import jira
from jira import Issue

from src.utils import jira as jira_utils
from src.utils.logging import get_logger

logger = get_logger(__name__)

JIRA_RECENT_COMMENT_LIMIT = 50
JIRA_USER_IDENTITY_FIELDS = ("accountId", "key", "name")


@dataclass(frozen=True)
class JiraIssue:
    key: str
    summary: str
    description: str
    status_name: str


@dataclass(frozen=True)
class JiraComment:
    id: str
    body: str
    author: dict[str, Any]
    created: str
    updated: str


class JiraIssueClient:
    def __init__(self, url: str, pat: str) -> None:
        if not pat:
            raise ValueError("Jira PAT must not be empty.")
        try:
            client = jira.JIRA(url, token_auth=pat, timeout=30)
            user = client.myself()
        except Exception as exc:
            raise RuntimeError("Failed to log in to Jira.") from exc
        try:
            identities = resolve_jira_user_identities(user)
        except ValueError as exc:
            raise RuntimeError(
                "Failed to resolve Jira service account identity."
            ) from exc
        self.client = client
        self.user_identities = identities

    def search_recent_issues(
        self,
        projects: Iterable[str],
        lookback_days: int,
        eligible_statuses: Iterable[str],
    ) -> Iterable[Issue]:
        project_filter = ", ".join(
            jira_utils.quote_jql_string(project) for project in projects
        )
        status_filter = ", ".join(
            jira_utils.quote_jql_string(status) for status in eligible_statuses
        )
        jql = (
            f"project in ({project_filter}) "
            f"AND status in ({status_filter}) "
            f'AND updated >= "-{lookback_days}d" '
            "ORDER BY updated ASC"
        )
        logger.info("Searching Jira issues with JQL: %s", jql)

        start_at = 0
        max_results = 100
        while True:
            batch = self.client.search_issues(
                jql,
                startAt=start_at,
                maxResults=max_results,
                fields=["summary", "description", "status"],
            )
            if not batch:
                break
            for issue in batch:
                yield issue
            if len(batch) < max_results:
                break
            start_at += max_results

    def post_restricted_comment(
        self, issue_key: str, body: str, visible_to_role: str
    ) -> Optional[str]:
        visibility = {"type": "role", "value": visible_to_role}
        created_comment = self.client.add_comment(
            issue_key, body, visibility=visibility
        )
        return extract_created_jira_comment_id(created_comment)

    def fetch_recent_comments(self, issue_key: str) -> list[JiraComment]:
        response = self.client._get_json(
            f"issue/{issue_key}/comment",
            params={
                "startAt": 0,
                "maxResults": JIRA_RECENT_COMMENT_LIMIT,
                "orderBy": "-created",
            },
        )
        comments = []
        for raw_comment in response["comments"]:
            comment = extract_jira_comment(raw_comment)
            if comment is not None:
                comments.append(comment)
        return comments

    def comment_mentions_authenticated_user(self, comment: JiraComment) -> bool:
        service_name = self.user_identities.get("name")
        if not service_name:
            return False
        return f"[~{service_name}]" in comment.body

    def comment_authored_by_authenticated_user(self, comment: JiraComment) -> bool:
        return same_jira_user(self.user_identities, comment.author)


def extract_issue(issue: Any) -> JiraIssue:
    fields = getattr(issue, "fields", None)
    if fields is None:
        raise ValueError("Jira issue is missing fields.")

    key = str(getattr(issue, "key", ""))
    if not key:
        raise ValueError("Jira issue is missing key.")

    status = getattr(fields, "status", None)
    status_name = str(getattr(status, "name", "") or "")
    if not status_name:
        raise ValueError(f"Jira issue {key} is missing status name.")

    return JiraIssue(
        key=key,
        summary=str(getattr(fields, "summary", "") or ""),
        description=str(getattr(fields, "description", "") or ""),
        status_name=status_name,
    )


def extract_jira_comment(raw_comment: dict[str, Any]) -> Optional[JiraComment]:
    comment_id = str(raw_comment.get("id") or "").strip()
    if not comment_id:
        return None

    author = raw_comment.get("author")
    if not isinstance(author, dict):
        return None
    if not any(
        jira_user_identity_value(author, field) for field in JIRA_USER_IDENTITY_FIELDS
    ):
        return None

    body = raw_comment.get("body")
    if not isinstance(body, str):
        return None

    return JiraComment(
        id=comment_id,
        body=body,
        author=author,
        created=str(raw_comment.get("created") or ""),
        updated=str(raw_comment.get("updated") or ""),
    )


def extract_created_jira_comment_id(created_comment: object) -> Optional[str]:
    try:
        comment_id = created_comment.id
    except AttributeError:
        return None
    if comment_id is None:
        return None
    text = str(comment_id).strip()
    if not text:
        return None
    return text


def resolve_jira_user_identities(user: dict[str, Any]) -> dict[str, str]:
    identities = {
        field: value
        for field in JIRA_USER_IDENTITY_FIELDS
        if (value := jira_user_identity_value(user, field))
    }
    if not identities:
        raise ValueError("Jira user payload must include accountId, key, or name.")
    return identities


def jira_user_identity_value(user: dict[str, Any], field: str) -> str:
    return str(user.get(field) or "").strip()


def same_jira_user(known_identities: dict[str, str], user: dict[str, Any]) -> bool:
    return any(
        jira_user_identity_value(user, field) == value
        for field, value in known_identities.items()
    )


__all__ = [
    "JIRA_RECENT_COMMENT_LIMIT",
    "JIRA_USER_IDENTITY_FIELDS",
    "JiraComment",
    "JiraIssue",
    "JiraIssueClient",
    "extract_created_jira_comment_id",
    "extract_issue",
    "extract_jira_comment",
    "jira_user_identity_value",
    "resolve_jira_user_identities",
    "same_jira_user",
]
