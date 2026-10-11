"""Translate ChatAgent.stream_chat() events into AG-UI protocol events (https://docs.ag-ui.com).

The library's events stay UI-agnostic; this adapter is the only AG-UI-specific code:

    run.started    -> RUN_STARTED
    tool.started   -> TOOL_CALL_START, TOOL_CALL_ARGS, TOOL_CALL_END   (the arguments are complete at start)
    tool.finished  -> TOOL_CALL_RESULT                                 (content = artifact as JSON, else the text)
    message.delta  -> TEXT_MESSAGE_START (first delta), TEXT_MESSAGE_CONTENT
    run.finished   -> TEXT_MESSAGE_END (or START/CONTENT/END if nothing streamed), RUN_FINISHED (with token usage)
    run.error      -> RUN_ERROR
"""

import json
from collections.abc import AsyncIterator
from uuid import uuid4

from ag_ui.core import (BaseEvent, RunAgentInput, RunErrorEvent, RunFinishedEvent, RunStartedEvent,
                        TextMessageContentEvent, TextMessageEndEvent, TextMessageStartEvent, ToolCallArgsEvent,
                        ToolCallEndEvent, ToolCallResultEvent, ToolCallStartEvent)
from ag_ui.core import TokenUsage


def _text(content) -> str:
    """AG-UI message content is a string or a list of parts; only the text parts matter here."""
    if isinstance(content, list):
        return "".join(getattr(part, "text", "") or "" for part in content)
    return content or ""


def chat_args(run_input: RunAgentInput) -> tuple[str, list[dict]]:
    """(latest user message, chat_history) for stream_chat() from the messages the AG-UI client sends.

    The client sends the whole conversation each run, including tool calls and results; only the user
    and assistant text is history for the agent.
    """
    turns = [{"role": m.role, "content": _text(m.content)} for m in run_input.messages
             if m.role in ("user", "assistant") and _text(m.content)]
    if not turns or turns[-1]["role"] != "user":
        raise ValueError("The last message must be a user message")
    return turns[-1]["content"], turns[:-1]


def _usage(usage: dict | None, model: str | None) -> list[TokenUsage] | None:
    if not usage:
        return None
    return [TokenUsage(model=model, input_tokens=usage["input_tokens"], output_tokens=usage["output_tokens"],
                       total_tokens=usage["total_tokens"],
                       reasoning_tokens=usage.get("output_token_details", {}).get("reasoning"),
                       cached_input_tokens=usage.get("input_token_details", {}).get("cache_read"))]


async def to_agui_events(events: AsyncIterator[dict], run_input: RunAgentInput,
                         model: str | None = None) -> AsyncIterator[BaseEvent]:
    """Yield AG-UI events for one run of stream_chat() events. Uses the client's thread and run ids."""
    thread_id, run_id = run_input.thread_id, run_input.run_id
    message_id = str(uuid4())
    text_started = False

    async for event in events:
        kind, data = event["type"], event["data"]

        if kind == "run.started":
            yield RunStartedEvent(thread_id=thread_id, run_id=run_id)

        elif kind == "tool.started":
            yield ToolCallStartEvent(tool_call_id=data["call_id"], tool_call_name=data["name"])
            yield ToolCallArgsEvent(tool_call_id=data["call_id"],
                                    delta=json.dumps(data["arguments"] or {}, ensure_ascii=False, default=str))
            yield ToolCallEndEvent(tool_call_id=data["call_id"])

        elif kind == "tool.finished":
            # AG-UI tool results are strings: send the artifact (UI data) as JSON when there is one
            if data["data"] is not None:
                content = json.dumps(data["data"], ensure_ascii=False, default=str)
            else:
                content = data["content"] if isinstance(data["content"], str) else json.dumps(data["content"], default=str)
            yield ToolCallResultEvent(message_id=str(uuid4()), tool_call_id=data["call_id"], content=content, role="tool")

        elif kind == "message.delta":
            if not text_started:
                text_started = True
                yield TextMessageStartEvent(message_id=message_id, role="assistant")
            yield TextMessageContentEvent(message_id=message_id, delta=data["text"])

        elif kind == "run.finished":
            # Fixed replies (direct/refuse intents) arrive only here, without deltas
            if not text_started and data["text"]:
                yield TextMessageStartEvent(message_id=message_id, role="assistant")
                yield TextMessageContentEvent(message_id=message_id, delta=data["text"])
                text_started = True
            if text_started:
                yield TextMessageEndEvent(message_id=message_id)
            yield RunFinishedEvent(thread_id=thread_id, run_id=run_id, usage=_usage(data.get("usage"), model))

        elif kind == "run.error":
            if text_started:
                yield TextMessageEndEvent(message_id=message_id)
            yield RunErrorEvent(message=data["message"], code=data["code"])
