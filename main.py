"""Test script for the ChatAgent from the agent_lib."""

import asyncio
import logging
import os

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool

from net.kairachat.agent.llm_agent import ChatAgent

load_dotenv()
logging.basicConfig(level=logging.INFO)


# --- Define some simple tools for the agent to use ---
@tool
def add(a: int, b: int) -> int:
    """Add two integers and return the result."""
    return a + b


@tool
def get_weather(city: str) -> str:
    """Return a fake weather report for the given city."""
    return f"The weather in {city} is sunny and 25°C."


async def main():
    # Make sure your model provider key is set, e.g.:
    #   setx OPENAI_API_KEY "sk-..."   (then restart the terminal)
    if not os.getenv("OPENAI_API_KEY"):
        print("WARNING: OPENAI_API_KEY is not set. The LLM call will fail.")

    config_path = os.path.join(os.path.dirname(__file__), "agent_config.json")

    agent = await ChatAgent.create(
        name="test-agent",
        config_path=config_path,
        tools=[add, get_weather],
    )

    # Build the initial state and invoke the agent node
    state = {
        "messages": [HumanMessage(content="What is 21 + 21, and what's the weather in Paris?")],
        "summary": "",
    }

    result = await agent.agent_node(state, config={"configurable": {}})

    print("\n=== Agent response ===")
    print(result["messages"][-1].content)


if __name__ == "__main__":
    asyncio.run(main())

