from __future__ import annotations

from typing import Any

from src.interfaces.jira import JiraComment, JiraIssue
from src.utils.logging import get_logger

logger = get_logger(__name__)

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


def build_mention_prompt(
    issue: JiraIssue,
    triggering_comment: JiraComment,
    comments: list[JiraComment],
    max_prompt_chars: int,
) -> str:
    if max_prompt_chars <= 0:
        raise ValueError("max_prompt_chars must be a positive integer.")

    prompt_prefix = (
        "Answer the Jira comment in the 'Triggering Comment:' section. "
        "Use the issue fields and recent comments as context.\n\n"
        "Issue:\n"
        f"{issue.key}\n\n"
        "Summary:\n"
        f"{issue.summary}\n\n"
        "Status:\n"
        f"{issue.status_name}\n\n"
        "Description:\n"
        f"{issue.description}\n\n"
        "Triggering Comment:\n"
        f"{format_jira_comment_for_prompt(triggering_comment)}\n\n"
        "Recent comments context (newest first):\n"
    )
    return prompt_prefix + format_jira_comments_context(
        comments,
        triggering_comment.id,
        prompt_prefix_chars=len(prompt_prefix),
        max_prompt_chars=max_prompt_chars,
    )


def format_jira_comments_context(
    comments: list[JiraComment],
    triggering_comment_id: str,
    *,
    prompt_prefix_chars: int,
    max_prompt_chars: int,
) -> str:
    if max_prompt_chars <= 0:
        raise ValueError("max_prompt_chars must be a positive integer.")
    if not comments:
        return "No recent comments were fetched."
    formatted_comments = []
    context_comments = []
    skipped_trigger_comment = False
    for comment in comments:
        if comment.id == triggering_comment_id:
            skipped_trigger_comment = True
        else:
            context_comments.append(comment)

    if not context_comments:
        if skipped_trigger_comment:
            return "No additional recent comments were fetched."
        return "No recent comments were fetched."

    current_chars = 0
    separator = "\n\n"
    omitted_count = 0
    for index, comment in enumerate(context_comments):
        formatted_comment = format_jira_comment_for_prompt(comment)
        next_chars = len(formatted_comment)
        if formatted_comments:
            next_chars += len(separator)
        if prompt_prefix_chars + current_chars + next_chars > max_prompt_chars:
            omitted_count = len(context_comments) - index
            logger.info(
                "Omitted %s older Jira comments from prompt because the "
                "model-derived prompt budget would be exceeded: "
                "prompt_chars=%s next_comment_chars=%s budget_chars=%s",
                omitted_count,
                prompt_prefix_chars + current_chars,
                next_chars,
                max_prompt_chars,
            )
            break
        formatted_comments.append(formatted_comment)
        current_chars += next_chars

    if not formatted_comments:
        return "No additional recent comments fit in the prompt budget."
    return separator.join(formatted_comments)


def format_jira_comment_for_prompt(comment: JiraComment) -> str:
    return (
        f"Comment ID: {comment.id}\n"
        f"Author: {format_jira_comment_author(comment.author)}\n"
        f"Created: {comment.created}\n"
        f"Updated: {comment.updated}\n"
        "Body:\n"
        f"{comment.body}"
    )


def format_jira_comment_author(author: dict[str, Any]) -> str:
    for field in ("displayName", "name", "key", "accountId"):
        value = str(author.get(field) or "").strip()
        if value:
            return f"{field}={value}"
    return "unknown"
