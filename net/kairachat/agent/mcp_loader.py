import logging
import os
import re

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient

logger = logging.getLogger(__name__)

_ENV_VAR = re.compile(r"\$\{([^}]+)\}")


def _expand_env(value):
    """Recursively replace ${VAR} in strings with the environment variable's value."""
    if isinstance(value, str):
        def replace(match):
            var_name = match.group(1)
            if var_name not in os.environ:
                raise ValueError(f"Environment variable '{var_name}' used in MCP server config is not set")
            return os.environ[var_name]

        return _ENV_VAR.sub(replace, value)
    if isinstance(value, dict):
        return {key: _expand_env(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand_env(item) for item in value]
    return value


async def load_mcp_tools_from_config(servers: dict | None) -> list[BaseTool]:
    """Load tools from the MCP servers declared under "mcp_servers" in the agent config.

    Each server entry is a langchain-mcp-adapters connection (transport, command/args or url/headers, ...)
    plus an optional "tools" list selecting which of the server's tools to load. Without "tools", all
    of the server's tools are loaded.
    """
    if not servers:
        return []

    connections = {}
    selected_tools = {}
    for server_name, server_config in servers.items():
        server_config = dict(server_config)
        selected_tools[server_name] = server_config.pop("tools", None)
        connections[server_name] = _expand_env(server_config)

    client = MultiServerMCPClient(connections)

    tools = []
    for server_name, wanted in selected_tools.items():
        try:
            server_tools = await client.get_tools(server_name=server_name)
        except Exception as e:
            raise RuntimeError(f"Failed to load tools from MCP server '{server_name}': {e}") from e

        available = {tool.name: tool for tool in server_tools}

        if wanted is None:
            logger.warning("MCP server '%s' has no 'tools' list in config; loading all %d tools",
                           server_name, len(server_tools))
            tools.extend(server_tools)
        else:
            missing = [tool_name for tool_name in wanted if tool_name not in available]
            if missing:
                raise ValueError(f"MCP server '{server_name}' has no tools named {missing}. "
                                 f"Available tools: {sorted(available)}")
            tools.extend(available[tool_name] for tool_name in wanted)

        logger.info("MCP server '%s': loaded tools %s", server_name,
                    wanted if wanted is not None else sorted(available))

    return tools
