import functools
import logging

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient

from net.kairachat.agent.utils import expand_env

logger = logging.getLogger(__name__)


def _with_json_artifact(tool: BaseTool) -> BaseTool:
    """Make the tool's artifact the MCP result's structuredContent itself (or None).

    langchain-mcp-adapters returns (content, {"structured_content": {...}}) when the server sends
    structuredContent (JSON data), else (content, None). Unwrapping it gives UIs the same plain JSON
    artifact as local and HTTP tools. Tools that return only text keep no artifact.
    """
    call_tool = tool.coroutine

    @functools.wraps(call_tool)  # keeps the signature, so injected args still reach the adapter
    async def call_tool_with_json_artifact(*args, **kwargs):
        content, artifact = await call_tool(*args, **kwargs)
        return content, (artifact or {}).get("structured_content")

    tool.coroutine = call_tool_with_json_artifact
    return tool


async def load_mcp_tools_from_config(servers: dict | None) -> list[BaseTool]:
    """Load tools from the MCP servers declared under "mcp_servers" in the agent config.

    Each server entry is a langchain-mcp-adapters connection (transport, command/args or url/headers, ...)
    plus an optional "tools" list selecting which of the server's tools to load. Without "tools", all
    of the server's tools are loaded. Tools whose results include JSON (MCP structuredContent) return it
    as their artifact for rich UIs; see mcp_ui_check.py to check which tools do.
    """
    if not servers:
        return []

    connections = {}
    selected_tools = {}
    for server_name, server_config in servers.items():
        server_config = dict(server_config)
        selected_tools[server_name] = server_config.pop("tools", None)
        connections[server_name] = expand_env(server_config)

    client = MultiServerMCPClient(connections)

    tools = []
    for server_name, wanted in selected_tools.items():
        try:
            server_tools = await client.get_tools(server_name=server_name)
        except Exception as e:
            raise RuntimeError(f"Failed to load tools from MCP server '{server_name}': {e}") from e

        available = {tool.name: _with_json_artifact(tool) for tool in server_tools}

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
