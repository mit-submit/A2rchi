from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

import jira
import psycopg2.extras
from jira import Issue

from src.archi.pipelines.agents import agent_spec as agent_spec_module
from src.archi.utils.output_dataclass import PipelineOutput
from src.utils import jira as jira_utils
from src.utils.conversation_service import Message
from src.utils.logging import get_logger
from src.utils.postgres_service_factory import PostgresServiceFactory
from src.utils.sql import SQL_CREATE_CONVERSATION, SQL_INSERT_CONVO

logger = get_logger(__name__)

DEFAULT_ELIGIBLE_STATUSES = ("Open", "In Progress")
JIRA_USER_IDENTITY_FIELDS = ("accountId", "key", "name")
JIRA_TRACE_SECTION_MAX_CHARS = 4000


@dataclass(frozen=True)
class JiraServiceConfig:
    url: str
    projects: list[str]
    visible_to_role: str
    poll_interval_minutes: int
    lookback_days: int
    eligible_statuses: list[str]

    @classmethod
    def from_config(cls, raw_config: dict) -> "JiraServiceConfig":
        if not isinstance(raw_config, dict) or not raw_config:
            raise ValueError(
                "Missing required config section: services.jira_ticket_responder"
            )

        required = ["url", "projects", "visible_to_role"]
        missing = [key for key in required if raw_config.get(key) in (None, "")]
        if missing:
            raise ValueError(
                f"Missing required services.jira_ticket_responder fields: {', '.join(missing)}"
            )

        projects = jira_utils.parse_jira_project_keys(
            raw_config["projects"],
            "services.jira_ticket_responder.projects must be a non-empty list of Jira project keys.",
        )

        poll_interval = cls._parse_positive_int(
            raw_config.get("poll_interval_minutes", 1),
            "poll_interval_minutes",
            1,
        )
        lookback_days = cls._parse_positive_int(
            raw_config.get("lookback_days", 7),
            "lookback_days",
            7,
        )
        eligible_statuses = raw_config.get("eligible_statuses") or list(
            DEFAULT_ELIGIBLE_STATUSES
        )

        url = str(raw_config["url"]).strip()
        visible_to_role = str(raw_config["visible_to_role"]).strip()
        if not url or not visible_to_role:
            raise ValueError(
                "services.jira_ticket_responder url and visible_to_role must not be empty."
            )

        return cls(
            url=url,
            projects=projects,
            visible_to_role=visible_to_role,
            poll_interval_minutes=poll_interval,
            lookback_days=lookback_days,
            eligible_statuses=eligible_statuses,
        )

    @staticmethod
    def _parse_positive_int(value: object, field_name: str, default: int) -> int:
        if value in (None, ""):
            value = default
        error = (
            f"services.jira_ticket_responder.{field_name} must be a positive integer."
        )
        if isinstance(value, bool):
            raise ValueError(error)
        try:
            parsed = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(error) from exc
        if parsed <= 0:
            raise ValueError(error)
        return parsed


@dataclass(frozen=True)
class JiraAgentSettings:
    agent_class: str
    agents_dir: Path
    default_provider: str
    default_model: str

    @property
    def model_provider(self) -> str:
        return f"{self.default_provider}/{self.default_model}"


@dataclass(frozen=True)
class JiraIssue:
    key: str
    summary: str
    description: str
    status_name: str


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
    ) -> None:
        visibility = {"type": "role", "value": visible_to_role}
        self.client.add_comment(issue_key, body, visibility=visibility)

    def has_comment_by_authenticated_user(self, issue_key: str) -> bool:
        start_at = 0
        max_results = 100
        while True:
            response = self.client._get_json(
                f"issue/{issue_key}/comment",
                params={
                    "startAt": start_at,
                    "maxResults": max_results,
                    "orderBy": "-created",
                },
            )
            comments = response["comments"]
            for comment in comments:
                if same_jira_user(self.user_identities, comment["author"]):
                    return True
            start_at += len(comments)
            if not comments or start_at >= int(response["total"]):
                break
        return False


class JiraTicketResponderService:
    def __init__(
        self,
        *,
        config: JiraServiceConfig,
        issue_client: JiraIssueClient,
        archi_instance: Any,
        postgres_factory: PostgresServiceFactory,
        agent_settings: JiraAgentSettings,
    ) -> None:
        self.config = config
        self.issue_client = issue_client
        self.archi = archi_instance
        self.postgres_factory = postgres_factory
        self.agent_settings = agent_settings

    def poll_once(self) -> None:
        for raw_issue in self.issue_client.search_recent_issues(
            self.config.projects,
            self.config.lookback_days,
            self.config.eligible_statuses,
        ):
            issue_key = str(getattr(raw_issue, "key", "<unknown>"))
            try:
                self.process_issue(raw_issue)
            except Exception:
                logger.error(
                    "Failed to process Jira issue %s", issue_key, exc_info=True
                )

    def process_issue(self, raw_issue: Any) -> bool:
        issue = extract_issue(raw_issue)
        if not is_issue_eligible(issue, self.config.eligible_statuses):
            return False

        try:
            has_existing_answer = self.issue_client.has_comment_by_authenticated_user(
                issue.key
            )
        except Exception:
            logger.error(
                "Failed to fetch Jira comments for issue %s.", issue.key, exc_info=True
            )
            return False
        if has_existing_answer:
            logger.debug(
                "Skipping Jira issue %s because the Jira service account already commented.",
                issue.key,
            )
            return False

        prompt = build_ticket_prompt(issue)
        try:
            result = self.archi(history=[("User", prompt)])
        except Exception:
            logger.error(
                "Archi failed while answering Jira issue %s", issue.key, exc_info=True
            )
            return False

        answer = extract_answer(result)
        if answer is None:
            logger.warning(
                "Skipping Jira issue %s because Archi returned no answer.", issue.key
            )
            return False

        jira_comment_body = build_jira_comment_body(answer, result)
        try:
            self.issue_client.post_restricted_comment(
                issue.key,
                jira_comment_body,
                self.config.visible_to_role,
            )
        except Exception:
            logger.error(
                "Failed to post Jira comment for issue %s", issue.key, exc_info=True
            )
            return False

        try:
            source_documents = getattr(result, "source_documents", []) or []
            self.persist_interaction(issue.key, prompt, answer, source_documents)
        except Exception:
            logger.error(
                "Failed to persist Jira interaction for issue %s after posting comment.",
                issue.key,
                exc_info=True,
            )
        return True

    def persist_interaction(
        self,
        issue_key: str,
        prompt: str,
        answer: str,
        source_documents: Iterable[Any],
    ) -> None:
        now = datetime.now(timezone.utc)
        title = f"Jira issue {issue_key}"
        client_id = "jira"
        archi_version = os.getenv("APP_VERSION", "unknown")
        link, context = format_source_context(source_documents)

        with self.postgres_factory.connection_pool.get_connection() as conn:
            try:
                with conn.cursor() as cursor:
                    cursor.execute(
                        SQL_CREATE_CONVERSATION,
                        (title, now, now, client_id, archi_version, None),
                    )
                    conversation_id = cursor.fetchone()[0]
                    messages = [
                        Message(
                            conversation_id=conversation_id,
                            sender="User",
                            content=prompt,
                            ts=now,
                            model_used=self.agent_settings.model_provider,
                            pipeline_used=self.agent_settings.agent_class,
                            archi_service="Jira",
                        ),
                        Message(
                            conversation_id=conversation_id,
                            sender="archi",
                            content=answer,
                            link=link,
                            context=context,
                            ts=now,
                            model_used=self.agent_settings.model_provider,
                            pipeline_used=self.agent_settings.agent_class,
                            archi_service="Jira",
                        ),
                    ]
                    values = [
                        (
                            message.archi_service,
                            message.conversation_id,
                            message.sender,
                            message.content,
                            message.link or "",
                            message.context or "",
                            message.ts,
                            message.model_used,
                            message.pipeline_used,
                        )
                        for message in messages
                    ]
                    psycopg2.extras.execute_values(cursor, SQL_INSERT_CONVO, values)
                conn.commit()
            except Exception:
                conn.rollback()
                raise


def resolve_jira_agent_settings(services_config: dict) -> JiraAgentSettings:
    jira_config = services_config.get("jira_ticket_responder", {}) or {}
    chat_config = services_config.get("chat_app", {}) or {}

    agent_class = jira_config.get("agent_class") or "CMSCompOpsAgent"
    agents_dir = Path(
        jira_config.get("agents_dir")
        or chat_config.get("agents_dir")
        or "/root/archi/agents"
    )
    default_provider = jira_config.get("default_provider") or chat_config.get(
        "default_provider"
    )
    default_model = jira_config.get("default_model") or chat_config.get("default_model")
    if not default_provider or not default_model:
        raise ValueError(
            "Jira ticket responder requires default_provider and default_model in services.jira_ticket_responder or services.chat_app."
        )

    return JiraAgentSettings(
        agent_class=str(agent_class),
        agents_dir=agents_dir,
        default_provider=str(default_provider),
        default_model=str(default_model),
    )


def build_archi_for_jira(
    services_config: dict,
    agent_settings: Optional[JiraAgentSettings] = None,
) -> tuple[Any, JiraAgentSettings]:
    from src.archi.archi import archi

    agent_settings = agent_settings or resolve_jira_agent_settings(services_config)
    try:
        agent_spec = agent_spec_module.select_agent_spec(agent_settings.agents_dir)
    except agent_spec_module.AgentSpecError as exc:
        raise ValueError(f"Failed to load Jira agent spec: {exc}") from exc

    archi_instance = archi(
        pipeline=agent_settings.agent_class,
        agent_spec=agent_spec,
        default_provider=agent_settings.default_provider,
        default_model=agent_settings.default_model,
    )
    return archi_instance, agent_settings


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


def is_issue_eligible(issue: JiraIssue, eligible_statuses: Iterable[str]) -> bool:
    if issue.status_name not in eligible_statuses:
        logger.debug(
            "Skipping Jira issue %s with status %s.", issue.key, issue.status_name
        )
        return False
    return True


def build_ticket_prompt(issue: JiraIssue) -> str:
    return (
        "Suggest a solution to this problem.\n\n"
        "Issue:\n"
        f"{issue.key}\n\n"
        "Summary:\n"
        f"{issue.summary}\n\n"
        "Status:\n"
        f"{issue.status_name}\n\n"
        "Description:\n"
        f"{issue.description}"
    )


def extract_answer(result: object) -> Optional[str]:
    answer = getattr(result, "answer", None)
    if not isinstance(answer, str):
        return None
    answer = answer.strip()
    if not answer:
        return None
    return answer


def build_jira_comment_body(answer: str, result: object) -> str:
    sections = []
    reasoning_trace = extract_reasoning_trace(result)
    if reasoning_trace:
        sections.append(
            format_jira_panel("Reasoning trace", format_jira_noformat(reasoning_trace))
        )

    tool_calls = extract_tool_calls_trace(result)
    if tool_calls:
        sections.append(format_tool_calls_panel(tool_calls))

    if not sections:
        return answer.strip()
    return "\n\n".join([answer.strip(), *sections])


def extract_reasoning_trace(result: object) -> str:
    if not isinstance(result, PipelineOutput):
        return ""

    reasoning_blocks = []
    for message in result.messages:
        additional_kwargs = getattr(message, "additional_kwargs", None) or {}
        reasoning_content = additional_kwargs.get("reasoning_content")
        if reasoning_content:
            reasoning_blocks.append(str(reasoning_content).strip())
    return "\n\n".join(block for block in reasoning_blocks if block)


def extract_tool_calls_trace(result: object) -> list[dict[str, Any]]:
    if not isinstance(result, PipelineOutput):
        return []
    return result.extract_tool_calls()


def format_tool_calls_panel(tool_calls: list[dict[str, Any]]) -> str:
    return format_jira_panel(
        "Tool calls", format_jira_noformat(format_tool_calls_trace(tool_calls))
    )


def format_tool_calls_trace(tool_calls: list[dict[str, Any]]) -> str:
    parts = []
    for index, tool_call in enumerate(tool_calls, start=1):
        tool_name = str(tool_call.get("name") or "unknown")
        tool_args = serialize_jira_trace_value(tool_call.get("args"))
        tool_result = serialize_jira_trace_value(tool_call.get("result"))
        parts.append(
            "\n".join(
                [
                    f"Tool call {index}: {tool_name}",
                    "Input:",
                    tool_args or "No input captured.",
                    "",
                    "Output:",
                    tool_result or "No output captured.",
                ]
            )
        )
    return "\n\n".join(parts)


def format_jira_panel(title: str, body: str) -> str:
    return f"{{panel:title={title}}}\n{body}\n{{panel}}"


def format_jira_noformat(value: str) -> str:
    text = sanitize_jira_noformat(truncate_jira_trace_text(value))
    return f"{{noformat}}\n{text}\n{{noformat}}"


def truncate_jira_trace_text(value: str) -> str:
    text = value.strip()
    if len(text) <= JIRA_TRACE_SECTION_MAX_CHARS:
        return text
    omitted = len(text) - JIRA_TRACE_SECTION_MAX_CHARS
    return (
        f"{text[:JIRA_TRACE_SECTION_MAX_CHARS].rstrip()}"
        f"\n\n[truncated {omitted} characters]"
    )


def sanitize_jira_noformat(value: str) -> str:
    return re.sub(r"\{noformat\}", "{ noformat }", value, flags=re.IGNORECASE)


def serialize_jira_trace_value(value: Any) -> str:
    if value in (None, "", {}, []):
        return ""
    if isinstance(value, str):
        return value.strip()
    return json.dumps(value, indent=2, sort_keys=True, default=str)


def format_source_context(source_documents: Iterable[Any]) -> tuple[str, str]:
    link = ""
    context_parts = []
    for index, document in enumerate(source_documents, start=1):
        metadata = getattr(document, "metadata", {}) or {}
        if not isinstance(metadata, dict):
            metadata = {}
        document_link = str(metadata.get("url") or "")
        if not link and document_link:
            link = document_link
        title = str(metadata.get("title") or metadata.get("display_name") or "No Title")
        content = str(getattr(document, "page_content", "") or "")
        context_parts.append(f"Source {index}: {title} ({document_link})\n\n{content}")
    return link, "\n\n\n\n".join(context_parts)
