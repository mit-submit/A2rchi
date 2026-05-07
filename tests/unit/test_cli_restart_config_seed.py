from pathlib import Path
from types import SimpleNamespace

from click.testing import CliRunner

from src.cli import cli_main


class _FakeConfigManager:
    def __init__(self, config_files, env):
        self._configs = {"config.yaml": {"services": {}}}

    def get_enabled_sources(self):
        return []

    def get_disabled_sources(self):
        return []

    def validate_configs(self, enabled_services, enabled_sources):
        return None

    def get_configs(self):
        return self._configs


class _FakeSecretsManager:
    def __init__(self, env_file, config_manager):
        pass

    def get_secrets(self, enabled_services, enabled_sources):
        return set(), set()

    def validate_secrets(self, required_secrets):
        return None

    def write_secrets_to_files(self, deployment_dir, all_secrets):
        return None


class _FakeTemplateManager:
    def __init__(self, env, verbosity):
        pass

    def prepare_deployment_files(self, compose_config, config_manager, secrets_manager, **kwargs):
        return None

    def copy_source_code(self, deployment_dir):
        return None


def test_restart_with_config_reruns_config_seed_before_chatbot(monkeypatch, tmp_path):
    deployment_dir = tmp_path / "archi-demo"
    deployment_dir.mkdir()
    (deployment_dir / "configs").mkdir()
    (deployment_dir / "compose.yaml").write_text(
        "services:\n"
        "  chatbot:\n"
        "    image: chatbot\n"
        "  config-seed:\n"
        "    image: config-seed\n"
    )

    config_path = tmp_path / "input.yaml"
    config_path.write_text("name: demo\nservices:\n  chat_app: {}\n")
    env_path = tmp_path / ".env"
    env_path.write_text("")

    calls = []

    class _FakeDeploymentManager:
        def __init__(self, use_podman=False):
            self.use_podman = use_podman

        def has_service(self, deployment_dir: Path, service_name: str) -> bool:
            return service_name == "config-seed"

        def run_service_once(self, deployment_dir: Path, service_name: str, build: bool = True, no_deps: bool = True):
            calls.append(("run", service_name, build, no_deps))

        def restart_service(self, deployment_dir: Path, service_name: str, build: bool = True, no_deps: bool = True, force_recreate: bool = True):
            calls.append(("restart", service_name, build, no_deps, force_recreate))

    monkeypatch.setattr(cli_main, "ARCHI_DIR", str(tmp_path))
    monkeypatch.setattr(cli_main, "check_docker_available", lambda: True)
    monkeypatch.setattr(cli_main, "ConfigurationManager", _FakeConfigManager)
    monkeypatch.setattr(cli_main, "SecretsManager", _FakeSecretsManager)
    monkeypatch.setattr(cli_main, "TemplateManager", _FakeTemplateManager)
    monkeypatch.setattr(cli_main, "DeploymentManager", _FakeDeploymentManager)
    monkeypatch.setattr(cli_main, "ServiceBuilder", SimpleNamespace(build_compose_config=lambda **kwargs: {}))
    monkeypatch.setattr(cli_main, "_load_rendered_configs", lambda configs_dir: {"config.yaml": {"services": {}}})
    monkeypatch.setattr(cli_main, "_infer_host_mode_from_compose", lambda compose_data: False)
    monkeypatch.setattr(cli_main, "_infer_gpu_ids_from_compose", lambda compose_data: None)
    monkeypatch.setattr(cli_main, "_infer_tag_from_compose", lambda compose_data: "2000")
    monkeypatch.setattr(cli_main, "_validate_non_chatbot_sections", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli_main.source_registry, "resolve_dependencies", lambda enabled_sources: enabled_sources)
    monkeypatch.setattr(cli_main.service_registry, "get_all_services", lambda: {"chatbot": object()})

    runner = CliRunner()
    result = runner.invoke(
        cli_main.restart,
        ["-n", "demo", "-s", "chatbot", "-c", str(config_path), "-e", str(env_path)],
    )

    assert result.exit_code == 0, result.output
    assert calls == [
        ("run", "config-seed", True, True),
        ("restart", "chatbot", True, True, True),
    ]
