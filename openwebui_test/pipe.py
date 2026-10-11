"""
title: ChatAgent (streaming + cards)
description: Streams replies from a ChatAgent /chat/stream endpoint and renders tool artifacts as HTML cards.
version: 0.1.0
requirements: httpx
"""

# Paste this whole file into Open WebUI: Admin Panel > Functions > + (new function).
# It does not import agent_lib; it only talks to the backend over HTTP.

import html
import json

import httpx
from pydantic import BaseModel, Field

# Reports the card height to Open WebUI, which renders embeds in a sandboxed iframe
HEIGHT_SCRIPT = """<script>
  function reportHeight() {
    parent.postMessage({ type: 'iframe:height', height: document.documentElement.scrollHeight }, '*');
  }
  window.addEventListener('load', reportHeight);
  new ResizeObserver(reportHeight).observe(document.body);
</script>"""

STYLE = """<style>
  body { margin: 0; font-family: system-ui, sans-serif; color: #1f2937; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 10px; padding: 4px; }
  .card { border: 1px solid #e5e7eb; border-radius: 10px; padding: 12px; background: #fff; }
  .card h3 { margin: 0 0 4px; font-size: 16px; }
  .muted { color: #6b7280; font-size: 12px; }
  .card ul { margin: 8px 0 0; padding-left: 18px; font-size: 13px; }
  .big { font-size: 28px; font-weight: 600; }
</style>"""


def page(body: str) -> str:
    return f"<!DOCTYPE html><html><head>{STYLE}</head><body>{body}{HEIGHT_SCRIPT}</body></html>"


def render_destinations(data: dict) -> str:
    cards = "".join(
        f"<article class='card'><h3>{html.escape(d['name'])}</h3>"
        f"<div class='muted'>{html.escape(d.get('region', ''))} Province</div>"
        f"<p>{html.escape(d.get('description', ''))}</p>"
        f"<ul>{''.join(f'<li>{html.escape(h)}</li>' for h in d.get('highlights', []))}</ul></article>"
        for d in data.get("destinations", [])
    )
    return page(f"<div class='grid'>{cards}</div>")


def render_weather(data: dict) -> str:
    return page(
        f"<div class='grid'><article class='card'><div class='muted'>Weather</div>"
        f"<h3>{html.escape(str(data['destination']))}</h3>"
        f"<div class='big'>{html.escape(str(data['temperature_c']))} °C</div>"
        f"<div>{html.escape(str(data['condition']))}</div></article></div>"
    )


# Tool name -> HTML renderer. Tools without a renderer still work; they just show no card.
RENDERERS = {
    "search_destinations": render_destinations,
    "get_weather": render_weather,
}


def text_of(content) -> str:
    # Open WebUI message content is a string, or a list of parts for multimodal messages
    if isinstance(content, list):
        return "".join(part.get("text", "") for part in content if isinstance(part, dict))
    return content or ""


class Pipe:
    class Valves(BaseModel):
        AGENT_URL: str = Field(default="http://host.docker.internal:8000",
                               description="Base URL of the ChatAgent backend")
        TIMEOUT_SECONDS: float = Field(default=120.0)

    def __init__(self):
        self.valves = self.Valves()

    async def pipe(self, body: dict, __event_emitter__=None, __metadata__=None, __task__=None):
        # Title/tag/follow-up generation also calls the selected model; don't run the agent for those
        if __task__:
            return ""

        messages = [m for m in body.get("messages", []) if m.get("role") in ("user", "assistant")]
        if not messages:
            return ""
        request = {
            "message": text_of(messages[-1]["content"]),
            "chat_id": (__metadata__ or {}).get("chat_id") or "openwebui",
            "chat_history": [{"role": m["role"], "content": text_of(m["content"])} for m in messages[:-1]],
        }
        return self._stream(request, __event_emitter__)

    async def _stream(self, request: dict, emit):
        async def emit_event(event):
            if emit:
                await emit(event)

        embeds = {}  # call_id -> html, so each tool result renders once
        streamed = False
        try:
            async with httpx.AsyncClient(timeout=self.valves.TIMEOUT_SECONDS) as client:
                async with client.stream("POST", f"{self.valves.AGENT_URL.rstrip('/')}/chat/stream",
                                         json=request, headers={"Accept": "text/event-stream"}) as response:
                    response.raise_for_status()
                    async for event in self._events(response):
                        kind, data = event["type"], event["data"]

                        if kind == "tool.started":
                            await emit_event({"type": "status", "data": {
                                "description": f"Running {data['name']}…", "done": False}})

                        elif kind == "tool.finished":
                            renderer = RENDERERS.get(data["name"])
                            if renderer and data["status"] == "success" and data["data"] is not None:
                                embeds[data["call_id"]] = renderer(data["data"])
                                await emit_event({"type": "embeds", "data": {"embeds": list(embeds.values())}})
                            await emit_event({"type": "status", "data": {
                                "description": f"{data['name']} finished", "done": True}})

                        elif kind == "message.delta":
                            streamed = True
                            yield data["text"]

                        elif kind == "run.finished":
                            # Fixed replies (direct/refuse intents) arrive only here, without deltas
                            if not streamed:
                                yield data["text"]
                            return

                        elif kind == "run.error":
                            yield f"\n\n⚠️ {data['message']}"
                            return

            yield "\n\n⚠️ The agent stream ended unexpectedly."
        except httpx.HTTPError as e:
            yield f"⚠️ Could not reach the agent at {self.valves.AGENT_URL}: {type(e).__name__}"

    @staticmethod
    async def _events(response):
        # Minimal SSE parser: frames are separated by a blank line; only "data:" lines carry the JSON event
        data_lines = []
        async for line in response.aiter_lines():
            if line == "":
                if data_lines:
                    event = json.loads("\n".join(data_lines))
                    data_lines = []
                    if event.get("schema_version") == "1":
                        yield event
            elif line.startswith("data:"):
                data_lines.append(line[5:].lstrip(" "))
