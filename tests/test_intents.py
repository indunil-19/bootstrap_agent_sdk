"""Test intent classification ("intent_classification" in the agent config).

Part 1 checks config validation (no LLM calls).
Part 2 runs a conversation and checks each message's intent, route and the tools that were called,
read from the agent's log lines.
"""

import asyncio
import json
import logging
import os
import re
import tempfile

from dotenv import load_dotenv
from langchain_core.tools import tool

from bootstrap_agent.llm_agent import ChatAgent

load_dotenv()
logging.basicConfig(level=logging.WARNING)
logging.getLogger("bootstrap_agent").setLevel(logging.INFO)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

OUT_OF_SCOPE_MESSAGE = "Sorry, I can only help with maths and weather questions."

INTENTS = {
    "math": {
        "description": "Arithmetic and maths calculations",
        "tools": ["add"],
        "instructions": "Always use the add tool for additions.",
    },
    "weather": {
        "description": "Weather conditions in a city",
        "tools": ["get_weather"],
    },
    "small_talk": {
        "description": "Greetings, thanks, goodbye and chit-chat",
        "route": "direct",
        "instructions": "Reply in one short friendly sentence.",
    },
    "out_of_scope": {
        "description": "Homework, essays, coding or other writing tasks",
        "route": "refuse",
        "message": OUT_OF_SCOPE_MESSAGE,
    },
    "politics": {
        "description": "Politics, elections and voting",
        "route": "refuse",
    },
}


@tool
def add(a: int, b: int) -> int:
    """Add two integers and return the result."""
    return a + b


@tool
def get_weather(city: str) -> str:
    """Return a fake weather report for the given city."""
    return f"The weather in {city} is sunny and 25°C."


@tool
def find_store(city: str) -> str:
    """Return the address of the store in the given city."""
    return f"Our {city} store is at 12 Main Street, open 9am-6pm."


class LogRecorder(logging.Handler):
    """Collects the intent, route and tool calls from the agent's log lines."""

    def __init__(self):
        super().__init__()
        self.reset()

    def reset(self):
        self.intent, self.route, self.tools = None, None, []

    def emit(self, record):
        message = record.getMessage()
        if match := re.search(r"classify_intent: (\w+) -> (\w+)", message):
            self.intent, self.route = match.groups()
        elif match := re.search(r"tool call -> (\w+)\(", message):
            self.tools.append(match.group(1))


results = {}


def check(name: str, passed: bool, detail: str = ""):
    results[name] = passed
    print(f"{'PASS' if passed else 'FAIL'}  {name}{f'  ({detail})' if detail else ''}")


def write_config(intent_classification: dict | None) -> str:
    with open(os.path.join(BASE_DIR, "../agent_config.json")) as f:
        config = json.load(f)
    config.pop("mcp_servers", None)
    config.pop("intent_classification", None)
    if intent_classification is not None:
        config["intent_classification"] = intent_classification

    config_file = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump(config, config_file)
    config_file.close()
    return config_file.name


async def expect_config_error(name: str, intent_classification: dict, expected_text: str):
    try:
        await ChatAgent.create(name="config-test", config_path=write_config(intent_classification), tools=[add, get_weather])
        check(name, False, "no error raised")
    except ValueError as e:
        check(name, expected_text in str(e), str(e))


async def config_tests():
    print("\n=== Part 1: config validation ===")
    await expect_config_error("reserved name 'none'",
                        {"enabled": True, "intents": {"none": {"description": "x"}}}, "reserved")
    await expect_config_error("unknown tool name",
                        {"enabled": True, "intents": {"math": {"description": "x", "tools": ["multiply"]}}}, "unknown tools")
    await expect_config_error("tools on a direct intent",
                        {"enabled": True, "intents": {"hi": {"description": "x", "route": "direct", "tools": ["add"]}}},
                        "only be used with the 'agent' route")
    await expect_config_error("unknown route",
                        {"enabled": True, "intents": {"hi": {"description": "x", "route": "handoff"}}}, "route must be one of")
    await expect_config_error("enabled with no intents", {"enabled": True, "intents": {}}, "no intents")

    disabled = await ChatAgent.create(name="disabled", config_path=write_config({"enabled": False, "intents": INTENTS}), tools=[add])
    no_block = await ChatAgent.create(name="no-block", config_path=write_config(None), tools=[add])
    check("enabled: false gives the same graph as no block",
          set(disabled.graph.nodes) == set(no_block.graph.nodes) and "classify_intent" not in disabled.graph.nodes,
          str(sorted(disabled.graph.nodes)))


async def conversation_test():
    print("\n=== Part 2: conversation ===")
    config_path = write_config({"enabled": True, "intents": INTENTS})
    agent = await ChatAgent.create(name="intent-test-agent", config_path=config_path, tools=[add, get_weather, find_store])

    recorder = LogRecorder()
    logging.getLogger("bootstrap_agent.llm_agent").addHandler(recorder)

    # (message, expected intent, expected route, allowed tools or None for any, extra check on the reply)
    cases = [
        ("Hi there!", "small_talk", "direct", set(), None),
        ("What is 21 + 21?", "math", "agent", {"add"}, lambda reply: "42" in reply),
        ("What's the weather in Paris?", "weather", "agent", {"get_weather"}, None),
        ("Write my homework essay about the French revolution.", "out_of_scope", "refuse", set(),
         lambda reply: reply == OUT_OF_SCOPE_MESSAGE),
        ("Who should I vote for in the next election?", "politics", "refuse", set(), None),
        ("Do you have a store in Colombo?", "none", "agent", None, None),
        ("Thanks!", "small_talk", "direct", set(), None),
    ]

    chat_history = []
    for message, expected_intent, expected_route, allowed_tools, reply_check in cases:
        recorder.reset()
        print(f"\nUser: {message}")
        reply = await agent.start_chat(message, chat_id="intent-test", chat_history=chat_history)
        print(f"Assistant: {reply}")
        chat_history += [{"role": "user", "content": message}, {"role": "assistant", "content": reply}]

        tools_ok = allowed_tools is None or set(recorder.tools) <= allowed_tools
        reply_ok = reply_check is None or reply_check(reply)
        check(f"'{message}'",
              recorder.intent == expected_intent and recorder.route == expected_route and tools_ok and reply_ok,
              f"intent={recorder.intent}, route={recorder.route}, tools={recorder.tools}")


async def main():
    await config_tests()
    await conversation_test()

    print("\n=== Summary ===")
    print(f"{sum(results.values())}/{len(results)} checks passed")
    assert all(results.values()), [name for name, passed in results.items() if not passed]


if __name__ == "__main__":
    asyncio.run(main())
