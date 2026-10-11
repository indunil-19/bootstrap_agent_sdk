"""Run a 21-turn conversation against the ChatAgent graph.

Once the chat history passed in exceeds 20 messages, should_continue() routes
through the summarize_conversation node, so this exercises the summarizer branch too.
"""

import asyncio
import logging
import os

from dotenv import load_dotenv
from langchain_core.tools import tool

from bootstrap_agent.llm_agent import ChatAgent

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

    agent = await ChatAgent.create(
        name="21-turn-test-agent",
        config_path=config_path,
        tools=[add, get_weather],
    )

    # The caller owns the chat history and passes it in on every turn
    chat_history = []

    prompts = [
        "Hi, my name is Indunil.",
        "What is 21 + 21?",
        "What's the weather in Paris?",
        "Add 100 and 250.",
        "What's the weather in Tokyo?",
        "What is 7 + 35?",
        "Tell me a fun fact about the number 42.",
        "Add 12 and 30.",
        "What's the weather in London?",
        "What is 5 + 5?",
        "What's the weather in Berlin?",
        "Add 1000 and 24.",
        "Give me another fun fact.",
        "What is 60 + 6?",
        "What's the weather in Madrid?",
        "Add 9 and 90.",
        "What's the weather in Rome?",
        "What is 3 + 3?",
        "Add 15 and 27.",
        "What's the weather in Oslo?",
        "What was my name again?",  # 21st turn - checks memory after summarization
    ]

    for i, prompt in enumerate(prompts, start=1):
        print(f"\n[Turn {i}] User: {prompt}")
        reply = await agent.start_chat(prompt, chat_id="21-turn-test-chat", chat_history=chat_history)
        print(f"[Turn {i}] Assistant: {reply}")
        chat_history += [{"role": "user", "content": prompt}, {"role": "assistant", "content": reply}]

    print(f"\nTotal messages in chat history: {len(chat_history)}")


if __name__ == "__main__":
    asyncio.run(main())

