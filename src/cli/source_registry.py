from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class SourceDefinition:
    """Definition for a data ingestion source."""

    name: str
    description: str
    required_secrets: List[str] = field(default_factory=list)
    required_config_fields: List[str] = field(default_factory=list)
    depends_on: List[str] = field(default_factory=list)


class SourceRegistry:
    """Registry that describes the supported data sources."""

    def __init__(self) -> None:
        self._sources: Dict[str, SourceDefinition] = {}
        self._register_defaults()

    def _register_defaults(self) -> None:
        self.register(
            SourceDefinition(
                name="links",
                description="Basic HTTP/HTTPS link scraping from input lists",
                required_config_fields=[
                    "data_manager.sources.links.input_lists",
                ],
            )
        )
        self.register(
            SourceDefinition(
                name="sso",
                description="SSO-backed web crawling",
                required_secrets=["SSO_USERNAME", "SSO_PASSWORD"],
                required_config_fields=[
                    "data_manager.sources.sso.sso_class",
                ],
                depends_on=["links"],
            )
        )
        self.register(
            SourceDefinition(
                name="git",
                description="Git repository scraping for MkDocs-based documentation",
                required_secrets=["GIT_USERNAME", "GIT_TOKEN"],
                depends_on=["links"],
            )
        )
        self.register(
            SourceDefinition(
                name="jira",
                description="Jira issue tracking integration",
                required_secrets=["JIRA_PAT"],
                required_config_fields=[
                    "data_manager.sources.jira.url",
                    "data_manager.sources.jira.projects",
                ],
            )
        )
        self.register(
            SourceDefinition(
                name="redmine",
                description="Redmine ticket integration",
                required_secrets=[
                    "REDMINE_USER",
                    "REDMINE_PW",
                ],
                required_config_fields=[
                    "data_manager.sources.redmine.url",
                    "data_manager.sources.redmine.project",
                ],
            )
        )

    def register(self, source_def: SourceDefinition) -> None:
        self._sources[source_def.name] = source_def

    def resolve_dependencies(self, sources: List[str]) -> List[str]:
        """Return sources including their dependency closure."""
        resolved_order: List[str] = []
        visited = set()

        def visit(name: str) -> None:
            if name in visited:
                return
            visited.add(name)
            source_def = self._sources.get(name)
            if not source_def:
                return
            for dep in source_def.depends_on:
                visit(dep)
            if name not in resolved_order:
                resolved_order.append(name)

        for src in sources:
            visit(src)

        return resolved_order

    def get(self, name: str) -> SourceDefinition:
        if name not in self._sources:
            raise KeyError(f"Unknown source: {name}")
        return self._sources[name]

    def names(self) -> List[str]:
        return sorted(self._sources.keys())

    def required_secrets(self, enabled_sources: List[str]) -> List[str]:
        secrets: List[str] = []
        for source in self.resolve_dependencies(enabled_sources):
            if source in self._sources:
                secrets.extend(self._sources[source].required_secrets)
        return sorted(set(secrets))

    def required_config_fields(self, enabled_sources: List[str]) -> List[str]:
        fields: List[str] = []
        for source in self.resolve_dependencies(enabled_sources):
            if source in self._sources:
                fields.extend(self._sources[source].required_config_fields)
        return sorted(set(fields))


source_registry = SourceRegistry()
