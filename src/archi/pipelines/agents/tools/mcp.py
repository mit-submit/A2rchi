from __future__ import annotations
import os
from typing import List, Any, Tuple, Optional

import httpx
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools
from langchain.tools import BaseTool

from src.utils.config_access import get_mcp_servers_config
from src.utils.logging import get_logger

logger = get_logger(__name__)

_CERN_CA_BUNDLE = "/etc/ssl/certs/tls-ca-bundle.pem"


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


async def initialize_mcp_client(user_id: Optional[str] = None) -> Tuple[Optional[MultiServerMCPClient], List[BaseTool]]:
    """
    Initializes the MCP client and fetches tool definitions.
    Args:
        user_id: SSO user ID used to look up a valid MCP OAuth token from the DB
                 for servers configured with sso_auth: true.
    Returns:
        client: The active client instance (must be kept alive by the caller).
        tools: The list of LangChain-compatible tools.
    """
    from src.utils.mcp_oauth_service import MCPOAuthService

    mcp_servers = get_mcp_servers_config()
    _mcp_oauth = MCPOAuthService()

    _use_cern_ca = os.path.exists(_CERN_CA_BUNDLE)
    if _use_cern_ca:
        logger.info(f"Using CERN CA bundle for MCP SSL verification: {_CERN_CA_BUNDLE}")

    # Resolve per-server config, injecting Bearer auth where sso_auth is enabled.
    # Skip SSO-auth servers when no valid MCP OAuth token is available.
    resolved_servers = {}
    for name, cfg in mcp_servers.items():
        server_cfg = dict(cfg)
        requires_sso = server_cfg.pop('sso_auth', False)
        if requires_sso:
            access_token = _mcp_oauth.get_access_token(user_id, name) if user_id else None
            if not access_token:
                logger.info(f"Skipping MCP server '{name}': sso_auth=true but no valid token for user_id={user_id!r}")
                continue
            server_cfg.setdefault('headers', {})['Authorization'] = f'Bearer {access_token}'

        # Inject CERN CA bundle via httpx_client_factory (SSE/streamable_http transports)
        if _use_cern_ca and server_cfg.get('transport') in ('sse', 'streamable_http'):
            server_cfg['httpx_client_factory'] = _make_httpx_factory(_CERN_CA_BUNDLE)

        resolved_servers[name] = server_cfg

    logger.info(f"Configuring MCP client with servers: {list(resolved_servers.keys())}")
    client = MultiServerMCPClient(resolved_servers)

    all_tools: List[BaseTool] = []
    failed_servers: dict[str, str] = {}

    for name in resolved_servers.keys():
        try:
            tools = await client.get_tools(server_name=name)
            for tool in tools:
                logger.info(f"Loaded tool from MCP server '{name}': {tool.name} - {tool.description}")
            all_tools.extend(tools)
        except Exception as e:
            logger.error(f"Failed to fetch tools from MCP server '{name}': {e}")
            failed_servers[name] = str(e)

    logger.info(f"Active MCP servers: {[n for n in resolved_servers if n not in failed_servers]}")
    logger.warning(f"Failed MCP servers: {list(failed_servers.keys())}")

    return client, all_tools
