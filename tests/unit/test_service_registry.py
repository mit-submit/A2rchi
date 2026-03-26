"""
Unit tests for the service registry.

Tests cover:
- Default service registration (infrastructure, application, integration)
- Service definition properties (volume names, image names, container names)
- Dependency resolution
- Secret aggregation
- Category filtering
- Custom service registration
"""

import pytest

from src.cli.service_registry import ServiceDefinition, ServiceRegistry


@pytest.fixture
def registry():
    """Fresh registry instance with default services."""
    return ServiceRegistry()


# ---------------------------------------------------------------------------
# Default services
# ---------------------------------------------------------------------------


class TestDefaultServices:

    def test_infrastructure_services_registered(self, registry):
        infra = registry.get_infrastructure_services()
        assert "data-manager" in infra
        assert "postgres" in infra

    def test_application_services_registered(self, registry):
        apps = registry.get_application_services()
        assert "chatbot" in apps
        assert "grafana" in apps
        assert "grader" in apps

    def test_integration_services_registered(self, registry):
        integrations = registry.get_integration_services()
        assert "piazza" in integrations
        assert "mattermost" in integrations
        assert "redmine-mailer" in integrations

    def test_all_services_returns_complete_set(self, registry):
        all_svc = registry.get_all_services()
        expected = {
            "data-manager",
            "postgres",
            "chatbot",
            "grafana",
            "grader",
            "piazza",
            "mattermost",
            "redmine-mailer",
            "benchmarking",
        }
        assert expected == set(all_svc.keys())


# ---------------------------------------------------------------------------
# ServiceDefinition properties
# ---------------------------------------------------------------------------


class TestServiceDefinition:

    def test_get_volume_name_with_pattern(self):
        svc = ServiceDefinition(
            name="postgres",
            description="DB",
            category="infrastructure",
            requires_volume=True,
            volume_name_pattern="archi-pg-{name}",
        )
        assert svc.get_volume_name("mybot") == "archi-pg-mybot"

    def test_get_volume_name_default_pattern(self):
        svc = ServiceDefinition(
            name="chatbot",
            description="Chat",
            category="application",
            requires_volume=True,
        )
        assert svc.get_volume_name("demo") == "archi-demo"

    def test_get_volume_name_none_when_not_required(self):
        svc = ServiceDefinition(
            name="mattermost",
            description="Mattermost",
            category="integration",
            requires_volume=False,
        )
        assert svc.get_volume_name("demo") is None

    def test_get_image_name(self):
        svc = ServiceDefinition(
            name="chatbot",
            description="Chat",
            category="application",
        )
        assert svc.get_image_name("prod") == "chatbot-prod"

    def test_get_image_name_none_when_no_image(self):
        svc = ServiceDefinition(
            name="external",
            description="External",
            category="integration",
            requires_image=False,
        )
        assert svc.get_image_name("prod") is None

    def test_get_container_name(self):
        svc = ServiceDefinition(
            name="chatbot",
            description="Chat",
            category="application",
        )
        assert svc.get_container_name("dev") == "chatbot-dev"


# ---------------------------------------------------------------------------
# Dependency resolution
# ---------------------------------------------------------------------------


class TestDependencyResolution:

    def test_chatbot_pulls_in_postgres(self, registry):
        resolved = registry.resolve_dependencies(["chatbot"])
        assert "postgres" in resolved

    def test_infrastructure_always_included(self, registry):
        resolved = registry.resolve_dependencies(["chatbot"])
        assert "data-manager" in resolved
        assert "postgres" in resolved

    def test_no_duplicates(self, registry):
        resolved = registry.resolve_dependencies(["chatbot", "grafana", "grader"])
        assert len(resolved) == len(set(resolved))

    def test_empty_input_still_includes_auto_enable(self, registry):
        resolved = registry.resolve_dependencies([])
        assert "data-manager" in resolved
        assert "postgres" in resolved

    def test_unknown_service_skipped(self, registry):
        resolved = registry.resolve_dependencies(["nonexistent"])
        infra = registry.get_infrastructure_services()
        for name in infra:
            assert name in resolved

    def test_grafana_pulls_in_postgres(self, registry):
        resolved = registry.resolve_dependencies(["grafana"])
        assert "postgres" in resolved


# ---------------------------------------------------------------------------
# Secret aggregation
# ---------------------------------------------------------------------------


class TestRequiredSecrets:

    def test_chatbot_no_required_secrets(self, registry):
        secrets = registry.get_required_secrets(["chatbot"])
        assert secrets == set()

    def test_grafana_requires_grafana_password(self, registry):
        secrets = registry.get_required_secrets(["grafana"])
        assert "GRAFANA_PG_PASSWORD" in secrets

    def test_piazza_secrets(self, registry):
        secrets = registry.get_required_secrets(["piazza"])
        assert "PIAZZA_EMAIL" in secrets
        assert "PIAZZA_PASSWORD" in secrets
        assert "SLACK_WEBHOOK" in secrets

    def test_multiple_services_union(self, registry):
        secrets = registry.get_required_secrets(["grafana", "piazza"])
        assert "GRAFANA_PG_PASSWORD" in secrets
        assert "PIAZZA_EMAIL" in secrets

    def test_unknown_service_ignored(self, registry):
        secrets = registry.get_required_secrets(["nonexistent"])
        assert secrets == set()


# ---------------------------------------------------------------------------
# Category filtering
# ---------------------------------------------------------------------------


class TestCategoryFiltering:

    def test_get_services_by_category(self, registry):
        infra = registry.get_services_by_category("infrastructure")
        assert all(s.category == "infrastructure" for s in infra.values())

    def test_unknown_category_returns_empty(self, registry):
        result = registry.get_services_by_category("nonexistent")
        assert result == {}


# ---------------------------------------------------------------------------
# Custom registration
# ---------------------------------------------------------------------------


class TestCustomRegistration:

    def test_register_new_service(self, registry):
        registry.register(
            ServiceDefinition(
                name="discord",
                description="Discord bot integration",
                category="integration",
                required_secrets=["DISCORD_TOKEN"],
            )
        )
        svc = registry.get_service("discord")
        assert svc.description == "Discord bot integration"
        assert "DISCORD_TOKEN" in svc.required_secrets

    def test_overwrite_existing_service(self, registry):
        registry.register(
            ServiceDefinition(
                name="chatbot",
                description="Updated chatbot",
                category="application",
            )
        )
        assert registry.get_service("chatbot").description == "Updated chatbot"


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:

    def test_get_unknown_service_raises(self, registry):
        with pytest.raises(ValueError, match="Unknown service"):
            registry.get_service("nonexistent_service")


# ---------------------------------------------------------------------------
# Port configuration
# ---------------------------------------------------------------------------


class TestPortConfiguration:

    def test_chatbot_default_ports(self, registry):
        svc = registry.get_service("chatbot")
        assert svc.default_host_port == 7861
        assert svc.default_container_port == 7861

    def test_data_manager_default_ports(self, registry):
        svc = registry.get_service("data-manager")
        assert svc.default_host_port == 7871
        assert svc.default_container_port == 7871

    def test_grafana_default_ports(self, registry):
        svc = registry.get_service("grafana")
        assert svc.default_host_port == 3000
        assert svc.default_container_port == 3000

    def test_postgres_has_no_default_ports(self, registry):
        svc = registry.get_service("postgres")
        assert svc.default_host_port is None
        assert svc.default_container_port is None
