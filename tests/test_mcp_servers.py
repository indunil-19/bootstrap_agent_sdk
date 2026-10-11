"""Test the MCP servers configured in agent_config.json via ChatAgent.create().

Checks that each server loads exactly the tools listed in its "tools" config, then asks
one question per server and checks that the agent called one of that server's tools.

Requires npx (Node.js) and uvx (uv); the first run downloads the server packages.
"""

import asyncio
import json
import logging
import os
import re

from dotenv import load_dotenv

from bootstrap_agent.llm_agent import ChatAgent

load_dotenv()
logging.basicConfig(level=logging.WARNING)
logging.getLogger("bootstrap_agent").setLevel(logging.INFO)

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../agent_config.json")

# One question per configured server, each needing one of that server's tools
PROMPTS = {
    "time": "What is the current time in Tokyo?",
    "fetch": "Fetch https://example.com and tell me the page heading.",
    "filesystem": "List the files in the current directory.",
    "memory": "Remember that Indunil's favourite language is Python, then search your memory for Indunil.",
    "deepwiki": "Using DeepWiki, what is the GitHub repo langchain-ai/langgraph about? One sentence.",
}


class ToolCallRecorder(logging.Handler):
    """Collects tool names from the agent's 'tool call -> name(args)' log lines."""

    def __init__(self):
        super().__init__()
        self.tool_names = []

    def emit(self, record):
        match = re.search(r"tool call -> (\w+)\(", record.getMessage())
        if match:
            self.tool_names.append(match.group(1))


async def main():
    with open(CONFIG_PATH) as f:
        mcp_servers = json.load(f).get("mcp_servers", {})

    agent = await ChatAgent.create(name="mcp-servers-test-agent", config_path=CONFIG_PATH)

    # 1. Each server should load exactly the tools listed in the config
    loaded = {tool.name for tool in agent.tools}
    expected = {tool_name for server in mcp_servers.values() for tool_name in server.get("tools", [])}
    assert loaded == expected, f"Loaded tools {sorted(loaded)} != configured tools {sorted(expected)}"
    print(f"\nLoaded {len(loaded)} tools from {len(mcp_servers)} servers: {sorted(loaded)}")

    recorder = ToolCallRecorder()
    logging.getLogger("bootstrap_agent.llm_agent").addHandler(recorder)

    # 2. Each server's question should call at least one of that server's tools
    results = {}
    for server_name, prompt in PROMPTS.items():
        if server_name not in mcp_servers:
            print(f"\n[{server_name}] skipped: not in agent_config.json")
            continue

        recorder.tool_names.clear()
        print(f"\n[{server_name}] User: {prompt}")
        reply = await agent.start_chat(prompt, chat_id=f"mcp-servers-test-{server_name}")
        print(f"[{server_name}] Assistant: {reply}")

        server_tools = set(mcp_servers[server_name].get("tools", []))
        used = [name for name in recorder.tool_names if name in server_tools]
        results[server_name] = bool(used)
        print(f"[{server_name}] tools called: {recorder.tool_names} -> {'PASS' if used else 'FAIL'}")

    print("\n=== Summary ===")
    for server_name, passed in results.items():
        print(f"{server_name:12} {'PASS' if passed else 'FAIL'}")

    assert all(results.values()), "Some servers' tools were not called"


if __name__ == "__main__":
    asyncio.run(main())
