from __future__ import annotations
import os
from typing import List, Any, Tuple, Optional

from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools
from langchain.tools import BaseTool

from src.utils.config_access import get_mcp_servers_config
from src.utils.logging import get_logger

logger = get_logger(__name__)

async def initialize_mcp_client() -> Tuple[Optional[MultiServerMCPClient], List[BaseTool]]:
    """
    Initializes the MCP client and fetches tool definitions.
    Returns:
        client: The active client instance (must be kept alive by the caller).
        tools: The list of LangChain-compatible tools.
    """

    mcp_servers = get_mcp_servers_config()

    # Strip archi-only fields that langchain-mcp-adapters doesn't understand.
    # These are consumed by the compose template (sidecars) or the legacy stdio
    # install path; the MCP client itself only knows about transport-specific fields.
    _archi_only_fields = {"env_from_secrets", "host_file_mounts", "build_context", "image", "path"}
    client_configs: dict[str, dict] = {}
    for name, server_cfg in mcp_servers.items():
        cfg = {k: v for k, v in server_cfg.items() if k not in _archi_only_fields}
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
        client_configs[name] = cfg

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

    return client, all_tools
