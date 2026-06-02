from types import SimpleNamespace

from src.cli.tools.config_seed import seed
from src.utils.config_service import StaticConfig


def _static_config_with_services(services_config):
    return StaticConfig(
        deployment_name="demo",
        config_version="1",
        data_path="/root/data",
        embedding_model="dummy",
        embedding_dimensions=384,
        chunk_size=500,
        chunk_overlap=50,
        distance_metric="cosine",
        global_config={"DATA_PATH": "/root/data"},
        services_config=services_config,
        data_manager_config={"sources": {}},
    )


class _FakeConfigService:
    def __init__(self, static_config=None):
        self._static_config = static_config
        self.seeded_services = None

    def get_static_config(self, force_reload=False):
        return self._static_config

    def initialize_static_config(self, **kwargs):
        self.seeded_services = kwargs["services_config"]
        return self._static_config or _static_config_with_services(kwargs["services_config"])

    def get_dynamic_config(self):
        return SimpleNamespace(updated_by="existing")


def test_seed_preserves_persisted_ab_testing_config_by_default():
    persisted_ab = {
        "enabled": True,
        "comparison_rate": 0.8,
        "variant_label_mode": "always_visible",
        "activity_panel_default_state": "expanded",
        "pool": {
            "champion": "Saved",
            "variants": [
                {"label": "Saved", "agent_spec": "saved.md"},
                {"label": "Candidate", "agent_spec": "candidate.md"},
            ],
        },
    }
    config = {
        "name": "demo",
        "global": {"DATA_PATH": "/root/data"},
        "data_manager": {"sources": {}},
        "services": {
            "chat_app": {
                "ab_testing": {
                    "enabled": True,
                    "comparison_rate": 0.2,
                    "variant_label_mode": "post_vote_reveal",
                    "activity_panel_default_state": "hidden",
                    "pool": {
                        "champion": "Yaml",
                        "variants": [
                            {"label": "Yaml", "agent_spec": "yaml.md"},
                            {"label": "Other", "agent_spec": "other.md"},
                        ],
                    },
                }
            }
        },
    }
    cs = _FakeConfigService(
        _static_config_with_services({"chat_app": {"ab_testing": persisted_ab}})
    )

    seed(config, cs)

    assert cs.seeded_services["chat_app"]["ab_testing"] == persisted_ab


def test_seed_preserves_persisted_ab_testing_when_yaml_omits_ab_block():
    persisted_ab = {
        "enabled": True,
        "comparison_rate": 0.8,
        "variant_label_mode": "always_visible",
        "activity_panel_default_state": "expanded",
    }
    config = {
        "name": "demo",
        "global": {"DATA_PATH": "/root/data"},
        "data_manager": {"sources": {}},
        "services": {
            "chat_app": {
                "default_provider": "openrouter",
            }
        },
    }
    cs = _FakeConfigService(
        _static_config_with_services({"chat_app": {"ab_testing": persisted_ab}})
    )

    seed(config, cs)

    assert cs.seeded_services["chat_app"]["ab_testing"] == persisted_ab


def test_seed_force_yaml_override_replaces_persisted_ab_testing_and_drops_flag():
    config = {
        "name": "demo",
        "global": {"DATA_PATH": "/root/data"},
        "data_manager": {"sources": {}},
        "services": {
            "chat_app": {
                "ab_testing": {
                    "enabled": True,
                    "force_yaml_override": True,
                    "comparison_rate": 0.2,
                    "variant_label_mode": "post_vote_reveal",
                    "activity_panel_default_state": "hidden",
                    "pool": {
                        "champion": "Yaml",
                        "variants": [
                            {"label": "Yaml", "agent_spec": "yaml.md"},
                            {"label": "Other", "agent_spec": "other.md"},
                        ],
                    },
                }
            }
        },
    }
    cs = _FakeConfigService(
        _static_config_with_services(
            {
                "chat_app": {
                    "ab_testing": {
                        "enabled": True,
                        "comparison_rate": 0.8,
                        "variant_label_mode": "always_visible",
                        "activity_panel_default_state": "expanded",
                    }
                }
            }
        )
    )

    seed(config, cs)

    assert cs.seeded_services["chat_app"]["ab_testing"]["comparison_rate"] == 0.2
    assert cs.seeded_services["chat_app"]["ab_testing"]["variant_label_mode"] == "post_vote_reveal"
    assert "force_yaml_override" not in cs.seeded_services["chat_app"]["ab_testing"]
