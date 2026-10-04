"""Test the ChatAgent using its compiled LangGraph via start_chat()."""

import asyncio
import logging
import os

from dotenv import load_dotenv
from langchain_core.tools import tool

from net.kairachat.agent.llm_agent import ChatAgent

load_dotenv()
logging.basicConfig(level=logging.INFO)


@tool
def add(a: int, b: int) -> int:
    """Add two integers and return the result."""
    return a + b


@tool
def get_weather(city: str) -> str:
    """Return a fake weather report for the given city."""
    return f"The weather in {city} is sunny and 25°C."


async def main():
    config_path = os.path.join(os.path.dirname(__file__), "agent_config.json")

    agent = ChatAgent(
        name="graph-test-agent",
        config_path=config_path,
        tools=[add, get_weather],
    )

    # Keep a single state so the conversation has memory across turns
    state = {"messages": [], "summary": ""}

    prompts = [
        "What is 21 + 21?",
        "And what's the weather in Paris?",
    ]

    for prompt in prompts:
        print(f"\nUser: {prompt}")
        reply = await agent.start_chat(prompt, state)
        print(f"Assistant: {reply}")


if __name__ == "__main__":
    asyncio.run(main())

