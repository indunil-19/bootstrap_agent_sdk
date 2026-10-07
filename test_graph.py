"""Test the ChatAgent using its compiled LangGraph via start_chat()."""

import asyncio
import logging
import os

from dotenv import load_dotenv
from langchain_core.runnables import RunnableConfig
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


@tool
def get_user_name(config: RunnableConfig) -> str:
    """Return the name of the current user."""
    return config["configurable"]["payload"].get("user_name", "unknown")


async def main():
    config_path = os.path.join(os.path.dirname(__file__), "agent_config.json")

    agent = await ChatAgent.create(
        name="graph-test-agent",
        config_path=config_path,
        tools=[add, get_weather, get_user_name],
    )

    # The caller owns the chat history and passes it in on every turn
    chat_history = []
    payload = {"user_name": "Indunil"}

    prompts = [
        "What is 21 + 21?",
        "And what's the weather in Paris?",
        "What is my name?",
    ]

    for prompt in prompts:
        print(f"\nUser: {prompt}")
        reply = await agent.start_chat(prompt, chat_id="graph-test-chat", payload=payload, chat_history=chat_history)
        print(f"Assistant: {reply}")
        chat_history += [{"role": "user", "content": prompt}, {"role": "assistant", "content": reply}]


if __name__ == "__main__":
    asyncio.run(main())

