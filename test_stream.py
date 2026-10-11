"""Test chat() and stream_chat(): tool artifacts for frontends (React, Open WebUI, AG-UI adapters)."""

import asyncio
import json
import logging
import os

from dotenv import load_dotenv
from langchain_core.tools import tool

from net.kairachat.agent.llm_agent import ChatAgent

load_dotenv()
logging.basicConfig(level=logging.WARNING)


@tool(response_format="content_and_artifact")
async def search_destinations(country: str):
    """Search travel destinations in a country."""
    items = [
        {"id": "ella", "name": "Ella", "description": "Mountains and hiking"},
        {"id": "galle", "name": "Galle", "description": "Fort and beaches"},
    ]
    # First element goes to the model, second is the artifact a frontend renders as cards
    return "Found Ella (mountains and hiking) and Galle (fort and beaches).", {"destinations": items}


async def main():
    config_path = os.path.join(os.path.dirname(__file__), "agent_config.json")
    agent = await ChatAgent.create(name="stream-test-agent", config_path=config_path, tools=[search_destinations])
    question = "Suggest some destinations in Sri Lanka."

    print("=== chat() ===")
    result = await agent.chat(question, chat_id="stream-test-chat")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    assert result["tool_outputs"] and result["tool_outputs"][0]["name"] == "search_destinations"

    print("\n=== stream_chat() ===")
    deltas = []
    types = []
    async for event in agent.stream_chat(question, chat_id="stream-test-chat"):
        types.append(event["type"])
        if event["type"] == "message.delta":
            deltas.append(event["data"]["text"])
        else:
            print(json.dumps(event, ensure_ascii=False))
    print(f"\nstreamed text ({len(deltas)} deltas): {''.join(deltas)}")

    assert types[0] == "run.started" and types[-1] == "run.finished"
    # The card data arrives before the final reply starts streaming
    assert types.index("tool.finished") < types.index("message.delta")


if __name__ == "__main__":
    asyncio.run(main())
