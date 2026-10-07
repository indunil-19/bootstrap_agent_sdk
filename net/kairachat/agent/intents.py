"""Intent classification config, validation and prompts.

Configured under "intent_classification" in the agent config. Each user message is classified into
exactly one configured intent or "none"; the intent's route decides how the message is handled:
"agent" (tools limited to the intent's tools), "direct" (reply without tools) or "refuse".
"none" goes to the normal agent with all tools.
"""

from typing import Literal

from pydantic import BaseModel, Field, create_model

NO_INTENT = "none"
ROUTES = ("agent", "direct", "refuse")
DEFAULT_HISTORY_MESSAGES = 6


def load_intent_settings(settings: dict | None, tool_names: list[str]) -> dict | None:
    """Validate the "intent_classification" config block. Returns None when classification is disabled."""
    if not settings or not settings.get("enabled", False):
        return None

    intents = settings.get("intents") or {}
    if not intents:
        raise ValueError("intent_classification is enabled but no intents are defined")

    for name, intent in intents.items():
        if name == NO_INTENT:
            raise ValueError(f"'{NO_INTENT}' is reserved and cannot be used as an intent name")
        if not intent.get("description"):
            raise ValueError(f"Intent '{name}': 'description' is required")

        route = intent.get("route", "agent")
        if route not in ROUTES:
            raise ValueError(f"Intent '{name}': route must be one of {list(ROUTES)}, got '{route}'")

        if "tools" in intent:
            if route != "agent":
                raise ValueError(f"Intent '{name}': 'tools' can only be used with the 'agent' route")
            unknown = [tool_name for tool_name in intent["tools"] if tool_name not in tool_names]
            if unknown:
                raise ValueError(f"Intent '{name}': unknown tools {unknown}. Available tools: {sorted(tool_names)}")

    return settings


def build_intent_model(intent_names: list[str]) -> type[BaseModel]:
    """Structured output model whose intent can only be one of the configured names or "none"."""
    return create_model(
        "IntentResult",
        intent=(Literal[tuple(intent_names + [NO_INTENT])], Field(description="The single best matching intent")),
        reason=(str, Field(description="One short sentence explaining the choice")),
    )


def build_classifier_prompt(intents: dict, summary: str) -> str:
    intent_lines = "\n".join(f"- {name}: {intent['description']}" for name, intent in intents.items())
    return f"""
Classify the user's latest message into exactly one of the intents below.
Use the earlier messages and the conversation summary only as context for understanding the latest message.
If no intent clearly fits the latest message, choose "{NO_INTENT}".

Intents:
{intent_lines}
- {NO_INTENT}: none of the intents above clearly fits the message.

Conversation summary so far:
{summary or "(No summary yet)"}
""".strip()


def build_refusal_prompt(intents: dict) -> str:
    capabilities = "\n".join(f"- {intent['description']}" for intent in intents.values()
                             if intent.get("route", "agent") != "refuse")
    return f"""
The user's latest message is outside what you can help with.
Politely decline in one or two sentences. Do not answer the question or do what it asks.
Briefly mention what you can help with instead:
{capabilities or "- (no other topics)"}
""".strip()
