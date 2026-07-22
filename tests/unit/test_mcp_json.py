import json

import pytest
import yaml

from src.cli.managers.config_manager import ConfigurationManager
from src.utils.mcp_json import expand_env_placeholders, load_mcp_json


# ---------------------------------------------------------------------------
# load_mcp_json: Claude-format parsing + translation into archi's schema
# ---------------------------------------------------------------------------

def _write_mcp_json(tmp_path, servers, filename=".mcp.json"):
    path = tmp_path / filename
    path.write_text(json.dumps({"mcpServers": servers}))
    return path


def test_load_translates_claude_types(tmp_path):
    path = _write_mcp_json(tmp_path, {
        "local-tools": {"type": "stdio", "command": "uvx", "args": ["mcp-server-example"]},
        "remote": {"type": "http", "url": "https://example.org/mcp",
                   "headers": {"Authorization": "Bearer ${TOKEN}"}},
        "events": {"type": "sse", "url": "http://localhost:8080/sse"},
    })
    servers = load_mcp_json(path)
    assert servers["local-tools"]["transport"] == "stdio"
    assert servers["local-tools"]["command"] == "uvx"
    assert "type" not in servers["local-tools"]
    assert servers["remote"]["transport"] == "streamable_http"
    assert servers["remote"]["headers"] == {"Authorization": "Bearer ${TOKEN}"}  # NOT expanded at load
    assert servers["events"]["transport"] == "sse"


def test_load_infers_type_from_fields(tmp_path):
    path = _write_mcp_json(tmp_path, {
        "inferred-stdio": {"command": "python", "args": ["-m", "server"]},
        "inferred-http": {"url": "https://example.org/mcp"},
    })
    servers = load_mcp_json(path)
    assert servers["inferred-stdio"]["transport"] == "stdio"
    assert servers["inferred-http"]["transport"] == "streamable_http"


def test_load_accepts_archi_native_transport_and_extras(tmp_path):
    path = _write_mcp_json(tmp_path, {
        "in-container": {
            "transport": "stdio", "command": "python", "args": ["-m", "pkg"],
            "path": "/host/pkg", "host_file_mounts": ["/host/cfg.json"], "skill": "pkg-skill",
        },
    })
    servers = load_mcp_json(path)
    cfg = servers["in-container"]
    assert cfg["transport"] == "stdio"
    assert cfg["path"] == "/host/pkg"
    assert cfg["host_file_mounts"] == ["/host/cfg.json"]
    assert cfg["skill"] == "pkg-skill"


@pytest.mark.parametrize("entry, match", [
    ({"type": "carrier-pigeon", "url": "x"}, "unsupported type"),
    ({"type": "stdio"}, "no 'command'"),
    ({"type": "http"}, "no 'url'"),
    ({"env": {"A": "b"}}, "needs a 'type'"),
    ("not-an-object", "must be an object"),
])
def test_load_rejects_bad_entries(tmp_path, entry, match):
    path = _write_mcp_json(tmp_path, {"bad": entry})
    with pytest.raises(ValueError, match=match):
        load_mcp_json(path)


def test_load_drops_underscore_comment_keys(tmp_path):
    path = _write_mcp_json(tmp_path, {
        "documented": {"_comment": "why this server exists", "type": "http", "url": "http://x/mcp"},
    })
    servers = load_mcp_json(path)
    assert "_comment" not in servers["documented"]


def test_load_rejects_missing_mcp_servers_key(tmp_path):
    path = tmp_path / ".mcp.json"
    path.write_text(json.dumps({"servers": {}}))
    with pytest.raises(ValueError, match="mcpServers"):
        load_mcp_json(path)


# ---------------------------------------------------------------------------
# expand_env_placeholders: ${VAR} / ${VAR:-default}
# ---------------------------------------------------------------------------

def test_expand_basic_and_default():
    env = {"TOKEN": "s3cret"}
    assert expand_env_placeholders("Bearer ${TOKEN}", env) == "Bearer s3cret"
    assert expand_env_placeholders("${MISSING:-fallback}", env) == "fallback"
    assert expand_env_placeholders("${MISSING:-}", env) == ""


def test_expand_recurses_and_preserves_non_strings():
    env = {"HOST": "example.org", "PORT": "8080"}
    cfg = {
        "url": "https://${HOST}:${PORT}/mcp",
        "headers": {"X-Token": "${MISSING:-anon}"},
        "args": ["--endpoint", "${HOST}"],
        "timeout": 30,
        "flag": True,
    }
    expanded = expand_env_placeholders(cfg, env)
    assert expanded["url"] == "https://example.org:8080/mcp"
    assert expanded["headers"] == {"X-Token": "anon"}
    assert expanded["args"] == ["--endpoint", "example.org"]
    assert expanded["timeout"] == 30 and expanded["flag"] is True


def test_expand_missing_without_default_raises():
    with pytest.raises(ValueError, match="NOPE"):
        expand_env_placeholders("${NOPE}", {})


def test_expand_leaves_non_placeholder_dollars_alone():
    env = {}
    assert expand_env_placeholders("cost is $5 and ${}", env) == "cost is $5 and ${}"


# ---------------------------------------------------------------------------
# ConfigurationManager: .mcp.json discovery
# ---------------------------------------------------------------------------

def _write_yaml(tmp_path, config, filename="config.yaml"):
    path = tmp_path / filename
    path.write_text(yaml.safe_dump(config))
    return path


def test_config_manager_merges_adjacent_mcp_json(tmp_path):
    config_path = _write_yaml(tmp_path, {"name": "test", "services": {}})
    _write_mcp_json(tmp_path, {"from-file": {"type": "http", "url": "http://x/mcp"}})
    manager = ConfigurationManager([str(config_path)], env=None)
    servers = manager.config["mcp_servers"]
    assert servers["from-file"]["transport"] == "streamable_http"


def test_config_manager_yaml_block_ignored(tmp_path):
    # A stale `mcp_servers:` block in the YAML is no longer read; the .mcp.json
    # fully defines the set.
    config_path = _write_yaml(tmp_path, {
        "name": "test", "services": {},
        "mcp_servers": {"yaml-only": {"transport": "stdio", "command": "python"}},
    })
    _write_mcp_json(tmp_path, {"from-file": {"type": "http", "url": "http://json/mcp"}})
    manager = ConfigurationManager([str(config_path)], env=None)
    assert set(manager.config["mcp_servers"]) == {"from-file"}


def test_config_manager_explicit_mcp_servers_file(tmp_path):
    _write_mcp_json(tmp_path, {"custom": {"type": "http", "url": "http://x/mcp"}},
                    filename="my-servers.json")
    config_path = _write_yaml(tmp_path, {
        "name": "test", "services": {}, "mcp_servers_file": "my-servers.json",
    })
    manager = ConfigurationManager([str(config_path)], env=None)
    assert "custom" in manager.config["mcp_servers"]


def test_config_manager_missing_explicit_file_fails_load(tmp_path):
    config_path = _write_yaml(tmp_path, {
        "name": "test", "services": {}, "mcp_servers_file": "does-not-exist.json",
    })
    # _load_config failures are caught per-file; with a single bad config the
    # manager ends up with no configs at all.
    with pytest.raises(AssertionError):
        ConfigurationManager([str(config_path)], env=None)


def test_config_manager_no_mcp_json_yields_empty(tmp_path):
    config_path = _write_yaml(tmp_path, {"name": "test", "services": {}})
    manager = ConfigurationManager([str(config_path)], env=None)
    assert manager.config["mcp_servers"] == {}


def test_config_manager_yaml_block_without_json_yields_empty(tmp_path):
    # No .mcp.json present: a YAML `mcp_servers:` block is dropped, not used.
    config_path = _write_yaml(tmp_path, {
        "name": "test", "services": {},
        "mcp_servers": {"yaml-only": {"transport": "stdio", "command": "python"}},
    })
    manager = ConfigurationManager([str(config_path)], env=None)
    assert manager.config["mcp_servers"] == {}
