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

    # For stdio transport servers, inject the current process environment so that
    # MCP subprocesses inherit env vars (e.g. RUCIO_HOST, X509_USER_PROXY).
    # Without this, mcp.client.stdio uses get_default_environment() which only
    # provides HOME and PATH.
    for name, server_cfg in mcp_servers.items():
        if server_cfg.get("transport") == "stdio":
            server_cfg["env"] = {**os.environ, **(server_cfg.get("env") or {})}

    logger.info(f"Configuring MCP client with servers: {list(mcp_servers.keys())}")
    client = MultiServerMCPClient(mcp_servers)

    all_tools: List[BaseTool] = []
    failed_servers: dict[str, str] = {}

    for name in mcp_servers.keys():
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

    logger.info(f"Active MCP servers: {[n for n in mcp_servers if n not in failed_servers]}")
    logger.warning(f"Failed MCP servers: {list(failed_servers.keys())}")

    return client, all_tools
