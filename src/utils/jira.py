import re

JIRA_PROJECT_KEY_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")


def parse_jira_project_keys(value: object, error_message: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(error_message)

    projects = []
    for project in value:
        if not isinstance(project, str):
            raise ValueError(error_message)
        project = project.strip()
        if not project or not JIRA_PROJECT_KEY_PATTERN.fullmatch(project):
            raise ValueError(error_message)
        projects.append(project)

    if not projects:
        raise ValueError(error_message)
    return projects


def quote_jql_string(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'
