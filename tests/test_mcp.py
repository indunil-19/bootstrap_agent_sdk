"""Test loading MCP tools from the agent config via ChatAgent.create().

Starts mcp_test_server.py over stdio. The config selects only two of its three
tools, so this also checks that unselected tools are not loaded. It also checks that the JSON data
(structuredContent) of MCP results becomes the tool artifact that rich UIs receive.
"""

import asyncio
import json
import logging
import os
import sys
import tempfile

from dotenv import load_dotenv
from langchain_core.tools import tool

from bootstrap_agent.llm_agent import ChatAgent

load_dotenv()
logging.basicConfig(level=logging.INFO)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


@tool
def add(a: int, b: int) -> int:
    """Add two integers and return the result."""
    return a + b


def write_config(mcp_servers: dict) -> str:
    """Write a temp config: agent_config.json plus the given mcp_servers."""
    with open(os.path.join(BASE_DIR, "../agent_config.json")) as f:
        config = json.load(f)
    config["mcp_servers"] = mcp_servers

    config_file = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump(config, config_file)
    config_file.close()
    return config_file.name


async def main():
    # sys.executable so the server runs with this venv's Python (which has the mcp package)
    server = {
        "transport": "stdio",
        "command": sys.executable,
        "args": [os.path.join(BASE_DIR, "mcp_test_server.py")],
    }

    config_path = write_config({"demo": {**server, "tools": ["multiply", "get_capital"]}})
    agent = await ChatAgent.create(name="mcp-test-agent", config_path=config_path, tools=[add])

    tool_names = sorted(t.name for t in agent.tools)
    print(f"\nLoaded tools: {tool_names}")
    assert tool_names == ["add", "get_capital", "multiply"], tool_names

    # MCP structuredContent becomes the plain JSON artifact (unwrapped), as for local and HTTP tools
    multiply = next(t for t in agent.tools if t.name == "multiply")
    message = await multiply.ainvoke({"type": "tool_call", "id": "1", "name": "multiply", "args": {"a": 6, "b": 7}})
    print(f"\nmultiply artifact: {message.artifact}")
    assert message.artifact == {"result": 42}, message.artifact

    result = await agent.chat("What is 6 times 7?", chat_id="mcp-test-chat")
    print(f"\nchat() tool_outputs: {result['tool_outputs']}")
    assert [(o["name"], o["data"]) for o in result["tool_outputs"]] == [("multiply", {"result": 42})], result

    for prompt in ["What is the capital of Japan?", "What is 10 + 5?"]:
        print(f"\nUser: {prompt}")
        print(f"Assistant: {await agent.start_chat(prompt, chat_id='mcp-test-chat')}")

    # A tool name that the server does not have should fail with a clear error
    bad_config_path = write_config({"demo": {**server, "tools": ["multiply", "no_such_tool"]}})
    try:
        await ChatAgent.create(name="bad-agent", config_path=bad_config_path, tools=[add])
        raise AssertionError("Expected ValueError for unknown MCP tool name")
    except ValueError as e:
        print(f"\nUnknown tool name rejected as expected: {e}")


if __name__ == "__main__":
    asyncio.run(main())
