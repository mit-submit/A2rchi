"""
Unit tests for the data-source registry.

Tests cover:
- Default source registration (links, sso, git, jira, redmine)
- Dependency resolution
- Secret and config-field aggregation
- Custom source registration
- Error handling for unknown sources
"""

import pytest

from src.cli.source_registry import SourceDefinition, SourceRegistry


@pytest.fixture
def registry():
    """Fresh registry instance with default sources."""
    return SourceRegistry()


# ---------------------------------------------------------------------------
# Default sources
# ---------------------------------------------------------------------------


class TestDefaultSources:

    def test_links_registered(self, registry):
        defn = registry.get("links")
        assert defn.name == "links"
        assert defn.required_secrets == []

    def test_sso_registered(self, registry):
        defn = registry.get("sso")
        assert "SSO_USERNAME" in defn.required_secrets
        assert "SSO_PASSWORD" in defn.required_secrets

    def test_git_registered(self, registry):
        defn = registry.get("git")
        assert "GIT_USERNAME" in defn.required_secrets
        assert "GIT_TOKEN" in defn.required_secrets

    def test_jira_registered(self, registry):
        defn = registry.get("jira")
        assert "JIRA_PAT" in defn.required_secrets

    def test_redmine_registered(self, registry):
        defn = registry.get("redmine")
        assert "REDMINE_USER" in defn.required_secrets
        assert "REDMINE_PW" in defn.required_secrets

    def test_names_returns_sorted_list(self, registry):
        names = registry.names()
        assert names == sorted(names)
        assert set(names) == {"git", "jira", "links", "redmine", "sso"}


# ---------------------------------------------------------------------------
# Dependency resolution
# ---------------------------------------------------------------------------


class TestDependencyResolution:

    def test_sso_pulls_in_links(self, registry):
        resolved = registry.resolve_dependencies(["sso"])
        assert "links" in resolved
        assert resolved.index("links") < resolved.index("sso")

    def test_git_pulls_in_links(self, registry):
        resolved = registry.resolve_dependencies(["git"])
        assert "links" in resolved

    def test_links_has_no_extra_deps(self, registry):
        resolved = registry.resolve_dependencies(["links"])
        assert resolved == ["links"]

    def test_jira_standalone(self, registry):
        resolved = registry.resolve_dependencies(["jira"])
        assert resolved == ["jira"]

    def test_multiple_sources_deduplicated(self, registry):
        resolved = registry.resolve_dependencies(["sso", "git", "links"])
        assert resolved.count("links") == 1

    def test_unknown_source_skipped(self, registry):
        resolved = registry.resolve_dependencies(["nonexistent"])
        assert resolved == []

    def test_empty_list_resolved(self, registry):
        assert registry.resolve_dependencies([]) == []


# ---------------------------------------------------------------------------
# Secret aggregation
# ---------------------------------------------------------------------------


class TestRequiredSecrets:

    def test_single_source_secrets(self, registry):
        secrets = registry.required_secrets(["jira"])
        assert "JIRA_PAT" in secrets

    def test_transitive_secrets_included(self, registry):
        secrets = registry.required_secrets(["sso"])
        assert "SSO_USERNAME" in secrets
        assert "SSO_PASSWORD" in secrets

    def test_no_duplicates(self, registry):
        secrets = registry.required_secrets(["sso", "git"])
        assert len(secrets) == len(set(secrets))

    def test_links_has_no_required_secrets(self, registry):
        assert registry.required_secrets(["links"]) == []


# ---------------------------------------------------------------------------
# Config field aggregation
# ---------------------------------------------------------------------------


class TestRequiredConfigFields:

    def test_links_config_fields(self, registry):
        fields = registry.required_config_fields(["links"])
        assert "data_manager.sources.links.input_lists" in fields

    def test_jira_config_fields(self, registry):
        fields = registry.required_config_fields(["jira"])
        assert "data_manager.sources.jira.url" in fields
        assert "data_manager.sources.jira.projects" in fields

    def test_no_duplicates(self, registry):
        fields = registry.required_config_fields(["links", "sso"])
        assert len(fields) == len(set(fields))


# ---------------------------------------------------------------------------
# Custom registration
# ---------------------------------------------------------------------------


class TestCustomRegistration:

    def test_register_new_source(self, registry):
        registry.register(
            SourceDefinition(
                name="confluence",
                description="Confluence wiki scraper",
                required_secrets=["CONFLUENCE_TOKEN"],
            )
        )
        defn = registry.get("confluence")
        assert defn.description == "Confluence wiki scraper"
        assert "confluence" in registry.names()

    def test_overwrite_existing_source(self, registry):
        registry.register(
            SourceDefinition(
                name="links",
                description="Updated links source",
            )
        )
        assert registry.get("links").description == "Updated links source"


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:

    def test_get_unknown_source_raises(self, registry):
        with pytest.raises(KeyError, match="Unknown source"):
            registry.get("nonexistent_source")
