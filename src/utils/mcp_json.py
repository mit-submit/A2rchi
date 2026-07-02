"""
Claude-style `.mcp.json` support.

MCP servers can be declared in a `.mcp.json` file next to a deployment config —
the same file format Claude Code and other MCP clients use — instead of (or in
addition to) the YAML config's `mcp_servers:` block:

    {
      "mcpServers": {
        "my-tools": {"type": "stdio", "command": "uvx", "args": ["mcp-server-example"]},
        "remote":   {"type": "http", "url": "https://example.org/mcp",
                     "headers": {"Authorization": "Bearer ${MCP_TOKEN}"}}
      }
    }

This module owns the format: parsing/normalizing the file into archi's internal
`mcp_servers` schema (Claude's `type` -> langchain's `transport`), and the
`${VAR}` / `${VAR:-default}` placeholder expansion applied to connection fields
at client-connect time. Archi-only fields (`path`, `host_file_mounts`,
`env_from_secrets`, `build_context`, `image`, `skill`) may appear in entries and
pass through untouched — MCP clients like Claude Code ignore unknown fields, so
one `.mcp.json` can serve both.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Mapping

from src.utils.logging import get_logger

logger = get_logger(__name__)

MCP_JSON_FILENAME = ".mcp.json"

# Claude-style `type` values -> langchain-mcp-adapters transport names. archi's
# native `transport` values are accepted too (identity entries).
_TYPE_TO_TRANSPORT = {
    "stdio": "stdio",
    "http": "streamable_http",
    "streamable-http": "streamable_http",
    "streamable_http": "streamable_http",
    "sse": "sse",
    "websocket": "websocket",
}

# ${VAR} or ${VAR:-default} (Claude Code's expansion syntax).
_PLACEHOLDER_RE = re.compile(
    r"\$\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?::-(?P<default>[^}]*))?\}"
)


def expand_env_placeholders(value: Any, env: Mapping[str, str]) -> Any:
    """Recursively expand ``${VAR}`` / ``${VAR:-default}`` in strings, lists and
    dict values; other types pass through unchanged.

    Raises ValueError naming the variable when it is unset and has no default,
    so a misconfigured server fails loudly at connect time instead of quietly
    sending a literal ``${TOKEN}`` in its URL or headers.
    """
    if isinstance(value, str):

        def _sub(match: re.Match) -> str:
            name = match.group("name")
            if name in env:
                return env[name]
            default = match.group("default")
            if default is not None:
                return default
            raise ValueError(
                f"environment variable '{name}' referenced as ${{{name}}} is not set "
                f"and has no ':-' default"
            )

        return _PLACEHOLDER_RE.sub(_sub, value)
    if isinstance(value, list):
        return [expand_env_placeholders(item, env) for item in value]
    if isinstance(value, dict):
        return {key: expand_env_placeholders(item, env) for key, item in value.items()}
    return value


def _normalize_server(name: str, entry: Any) -> Dict[str, Any]:
    """Translate one mcpServers entry into archi's internal server schema."""
    if not isinstance(entry, dict):
        raise ValueError(f"mcpServers['{name}'] must be an object, got {type(entry).__name__}")
    # JSON has no comment syntax; keys starting with "_" are documentation and
    # are dropped here so they never reach the MCP client.
    cfg = {key: value for key, value in entry.items() if not key.startswith("_")}
    raw = cfg.pop("type", None) or cfg.get("transport")
    if raw is None:
        # Claude Code infers stdio from `command`; extend the same courtesy to `url`.
        raw = "stdio" if "command" in cfg else ("http" if "url" in cfg else None)
    if raw is None:
        raise ValueError(
            f"mcpServers['{name}'] needs a 'type' ('stdio', 'http' or 'sse'), "
            f"or a 'command'/'url' field to infer it from"
        )
    transport = _TYPE_TO_TRANSPORT.get(str(raw).strip().lower())
    if transport is None:
        raise ValueError(
            f"mcpServers['{name}'] has unsupported type/transport '{raw}' "
            f"(supported: {sorted(set(_TYPE_TO_TRANSPORT))})"
        )
    cfg["transport"] = transport
    if transport == "stdio" and not cfg.get("command"):
        raise ValueError(f"mcpServers['{name}'] is a stdio server but has no 'command'")
    if transport != "stdio" and not cfg.get("url"):
        raise ValueError(f"mcpServers['{name}'] is a {transport} server but has no 'url'")
    return cfg


def load_mcp_json(path: Path) -> Dict[str, Dict[str, Any]]:
    """Load a Claude-style .mcp.json and return archi-schema server definitions.

    Placeholders are NOT expanded here: the file is read on the deploy host, but
    ``${VAR}`` values (tokens, endpoints) belong to the runtime container env —
    expansion happens in initialize_mcp_client, so secrets never get baked into
    rendered configs.
    """
    path = Path(path)
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read MCP config {path}: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("mcpServers"), dict):
        raise ValueError(
            f"{path} must be a JSON object with an 'mcpServers' object "
            f"(the Claude .mcp.json format)"
        )
    servers = {
        name: _normalize_server(name, entry) for name, entry in data["mcpServers"].items()
    }
    logger.info("Loaded %d MCP server(s) from %s: %s", len(servers), path, sorted(servers))
    return servers
