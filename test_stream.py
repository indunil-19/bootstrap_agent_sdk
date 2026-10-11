"""Test chat() and stream_chat(): tool artifacts for frontends (React, Open WebUI, AG-UI adapters) and token usage."""

import asyncio
import json
import logging
import os
import tempfile

from dotenv import load_dotenv
from langchain_core.tools import tool

from bootstrap_agent.llm_agent import ChatAgent

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
    check_usage(result["usage"])

    print("\n=== stream_chat() ===")
    deltas = []
    types = []
    finished = None
    async for event in agent.stream_chat(question, chat_id="stream-test-chat"):
        types.append(event["type"])
        if event["type"] == "message.delta":
            deltas.append(event["data"]["text"])
        elif event["type"] == "run.finished":
            finished = event["data"]
            print(json.dumps(event, ensure_ascii=False))
        else:
            print(json.dumps(event, ensure_ascii=False))
    print(f"\nstreamed text ({len(deltas)} deltas): {''.join(deltas)}")

    assert types[0] == "run.started" and types[-1] == "run.finished"
    # The card data arrives before the final reply starts streaming
    assert types.index("tool.finished") < types.index("message.delta")
    # The streamed reply (response_generator) is counted too, thanks to stream_usage
    check_usage(finished["usage"])

    # "token_usage": false turns counting off: no callback, usage None, stream_usage left as is
    with open(config_path) as f:
        config = {**json.load(f), "token_usage": False}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(config, f)
    off = await ChatAgent.create(name="no-usage-agent", config_path=f.name, tools=[search_destinations])
    _, run_config, usage = off._prepare_turn("hi", "c")
    assert usage is None and "callbacks" not in run_config
    print("\ntoken_usage false: no usage counter, as expected")


def check_usage(usage: dict):
    print(f"usage: {json.dumps(usage)}")
    assert {"agent", "response_generator"} <= set(usage["by_step"]), usage
    assert usage["total_tokens"] == sum(step["total_tokens"] for step in usage["by_step"].values()) > 0
    assert usage["llm_calls"] >= 3  # agent: tool call + answer; response_generator: reply


if __name__ == "__main__":
    asyncio.run(main())
