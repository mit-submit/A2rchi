from __future__ import annotations
import os
from typing import Any, Dict, List, Optional, Tuple

import httpx
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools
from langchain.tools import BaseTool

from src.utils.config_access import get_mcp_servers_config, get_full_config
from src.utils.env import read_secret, ssl_verify
from src.utils.logging import get_logger
from src.archi.pipelines.agents.utils.skill_utils import load_skill

logger = get_logger(__name__)

# Marker remote archi deployments put on state-changing tool descriptions.
_WRITE_MARKER = "[WRITE OPERATION]"


def _make_httpx_factory(ca_bundle: str):
    """Return an httpx_client_factory that uses the given CA bundle for SSL verification."""
    def factory(
        headers: dict | None = None,
        timeout: httpx.Timeout | None = None,
        auth: httpx.Auth | None = None,
    ) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            headers=headers or {},
            timeout=timeout,
            auth=auth,
            verify=ca_bundle,
            follow_redirects=True,
        )
    return factory


def _prepare_server_configs(
    user_id: Optional[str] = None,
) -> Tuple[Dict[str, dict], Dict[str, str], Dict[str, Dict[str, Any]]]:
    """
    Shared auth gating + client-config prep for all configured MCP servers.

    Returns:
        client_configs: server name -> MultiServerMCPClient connection config,
            only for servers that passed their auth gate.
        server_skills: server name -> loaded skill content (gated servers excluded).
        statuses: server name -> {url, transport, auth, state, detail} for ALL
            configured servers, including gated ones. States at this stage:
            'configured' (in client_configs), 'needs_auth' (sso_auth without a
            token), 'headless_only' (service_auth on a user-scoped request),
            'no_secret' (service_auth without its token secret).
    """
    from src.utils.mcp_oauth_service import MCPOAuthService

    mcp_servers = get_mcp_servers_config()
    _mcp_oauth = MCPOAuthService()

    _verify = ssl_verify()
    _use_ca_bundle = isinstance(_verify, str)
    if _use_ca_bundle:
        logger.info(f"Using CA bundle for MCP SSL verification: {_verify}")

    # Strip archi-only fields that langchain-mcp-adapters doesn't understand.
    # These are consumed by the compose template (sidecars), the legacy stdio
    # install path, or post-load tool customization — the MCP client itself only
    # knows about transport-specific fields.
    _archi_only_fields = {
        "env_from_secrets", "host_file_mounts", "build_context", "image", "path", "skill",
    }
    client_configs: Dict[str, dict] = {}
    server_skills: Dict[str, str] = {}
    statuses: Dict[str, Dict[str, Any]] = {}
    full_config = get_full_config()
    for name, server_cfg in mcp_servers.items():
        cfg = {k: v for k, v in server_cfg.items() if k not in _archi_only_fields}

        requires_sso = cfg.pop("sso_auth", False)
        service_auth = cfg.pop("service_auth", False)
        token_secret = cfg.pop("token_secret", None)
        if requires_sso and service_auth:
            logger.warning(
                f"MCP server '{name}': both sso_auth and service_auth set; "
                f"using per-user sso_auth and ignoring service_auth"
            )
            service_auth = False

        status = {
            "url": server_cfg.get("url", ""),
            "transport": server_cfg.get("transport", ""),
            "auth": "sso" if requires_sso else ("service" if service_auth else "none"),
            "state": "configured",
            "detail": "",
        }
        statuses[name] = status

        # Inject Bearer auth where sso_auth is enabled. Skip SSO-auth servers
        # when no valid MCP OAuth token is available for this user.
        if requires_sso:
            access_token = _mcp_oauth.get_access_token(user_id, name) if user_id else None
            if not access_token:
                logger.info(f"Skipping MCP server '{name}': sso_auth=true but no valid token for user_id={user_id!r}")
                status["state"] = "needs_auth"
                status["detail"] = (
                    "No valid token for this user - authorize the server to connect"
                    if user_id else "Requires a per-user token - sign in and authorize"
                )
                continue
            cfg.setdefault("headers", {})["Authorization"] = f"Bearer {access_token}"
        elif service_auth:
            # Deployment-level static Bearer token, for contexts with no user
            # identity (mattermost/benchmarking/cron and the startup registry).
            # Deliberately NOT attached to per-user requests: actions would be
            # attributed to the service account on the remote, and a per-user
            # twin of the same server would collide on tool names.
            if user_id:
                logger.info(
                    f"Skipping MCP server '{name}': service_auth servers are "
                    f"headless-only (request has user identity)"
                )
                status["state"] = "headless_only"
                status["detail"] = "Service-account server, available to headless interfaces only"
                continue
            secret_name = token_secret or f"{name.upper()}_MCP_TOKEN"
            service_token = read_secret(secret_name)
            if not service_token:
                logger.info(f"Skipping MCP server '{name}': service_auth=true but secret '{secret_name}' is not set")
                status["state"] = "no_secret"
                status["detail"] = f"Service token secret '{secret_name}' is not set"
                continue
            cfg.setdefault("headers", {})["Authorization"] = f"Bearer {service_token}"

        # Load any declared skill so we can append it to this server's tool descriptions.
        # Done after the auth gates so skipped servers contribute no skill text.
        skill_name = server_cfg.get("skill")
        if skill_name:
            skill_content = load_skill(skill_name, full_config)
            if skill_content:
                server_skills[name] = skill_content

        transport = cfg.get("transport")
        if transport == "stdio":
            # stdio subprocesses inherit nothing by default (mcp.client.stdio uses
            # an empty env). Forward the parent process env so stdio MCP servers see
            # what they need.
            cfg["env"] = {**os.environ, **(cfg.get("env") or {})}
        else:
            # For HTTP-based transports, `env` is for the sidecar container (compose),
            # not the MCP client connection — drop it here.
            cfg.pop("env", None)

        # Inject the CA bundle via httpx_client_factory (SSE/streamable_http transports)
        if _use_ca_bundle and transport in ("sse", "streamable_http"):
            cfg["httpx_client_factory"] = _make_httpx_factory(_verify)

        client_configs[name] = cfg

    return client_configs, server_skills, statuses


async def initialize_mcp_client(user_id: Optional[str] = None) -> Tuple[Optional[MultiServerMCPClient], List[BaseTool], str]:
    """
    Initializes the MCP client and fetches tool definitions.
    Args:
        user_id: SSO user ID used to look up a valid MCP OAuth token from the DB
                 for servers configured with sso_auth: true.
    Returns:
        client: The active client instance (must be kept alive by the caller).
        tools: The list of LangChain-compatible tools.
        skills_text: Concatenated skill content from all MCP servers that declare
            a `skill`. Empty string if no server has a skill. The caller is
            responsible for appending this to the agent's system prompt — we inject
            here only once per agent rather than into each tool description, so
            the content doesn't multiply by tool count.
    """
    client_configs, server_skills, _ = _prepare_server_configs(user_id)

    logger.info(f"Configuring MCP client with servers: {list(client_configs.keys())}")
    client = MultiServerMCPClient(client_configs)

    all_tools: List[BaseTool] = []
    failed_servers: dict[str, str] = {}

    for name in client_configs.keys():
        try:
            tools = await client.get_tools(server_name=name)
            for tool in tools:
                # Return error messages to the LLM instead of crashing the agent chain.
                tool.handle_tool_error = True
                logger.info(f"Loaded tool from MCP server '{name}': {tool.name} - {tool.description}")
            all_tools.extend(tools)
        except Exception as e:
            logger.error(f"Failed to fetch tools from MCP server '{name}': {e}")
            failed_servers[name] = str(e)

    logger.info(f"Active MCP servers: {[n for n in client_configs if n not in failed_servers]}")
    logger.warning(f"Failed MCP servers: {list(failed_servers.keys())}")

    # Build a single combined skills block keyed by server name — this is appended
    # to the agent's system prompt once, rather than duplicated across every tool.
    skills_parts: List[str] = []
    for name, skill_content in server_skills.items():
        if name not in failed_servers:
            skills_parts.append(
                f"\n--- {name} MCP Server Domain Knowledge ---\n{skill_content}"
            )
    skills_text = "".join(skills_parts)

    return client, all_tools, skills_text


def has_user_scoped_servers() -> bool:
    """
    True when any configured MCP server's toolset depends on who is asking
    (sso_auth or service_auth) — i.e. a per-user agent build can differ from
    the shared anonymous one. Lets callers skip per-request rebuilds entirely
    for deployments with only public MCP servers.
    """
    return any(
        cfg.get("sso_auth") or cfg.get("service_auth")
        for cfg in (get_mcp_servers_config() or {}).values()
    )


async def get_mcp_server_status(user_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Per-server status snapshot for the UI: auth mode, availability and, for
    reachable servers, the tool list. Unlike initialize_mcp_client this keeps
    no client alive — sessions are opened per server just to list tools.

    States: 'active' (connected, tools listed), 'needs_auth', 'headless_only',
    'no_secret', 'failed' (auth gate passed but the server errored).
    """
    import asyncio

    client_configs, _, statuses = _prepare_server_configs(user_id)

    async def _list_tools(client: MultiServerMCPClient, name: str) -> None:
        status = statuses[name]
        try:
            tools = await client.get_tools(server_name=name)
            status["state"] = "active"
            status["tools"] = [
                {
                    "name": t.name,
                    # Strip the write marker here so the UI never needs to
                    # know the convention — it just reads the `write` flag.
                    "description": (t.description or "").replace(_WRITE_MARKER, "").strip(),
                    "write": _WRITE_MARKER in (t.description or ""),
                }
                for t in tools
            ]
        except Exception as e:
            logger.error(f"MCP status: failed to fetch tools from '{name}': {e}")
            status["state"] = "failed"
            status["detail"] = str(e)[:300]

    if client_configs:
        client = MultiServerMCPClient(client_configs)
        # Concurrent: panel latency is the slowest server, not the sum of all.
        await asyncio.gather(*(_list_tools(client, name) for name in client_configs))

    return [{"name": name, **status} for name, status in statuses.items()]
