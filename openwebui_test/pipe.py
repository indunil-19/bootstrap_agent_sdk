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
  .card img { width: 100%; height: 110px; object-fit: cover; border-radius: 6px; margin-bottom: 8px; }
</style>"""


def page(body: str) -> str:
    return f"<!DOCTYPE html><html><head><meta charset='utf-8'>{STYLE}</head><body>{body}{HEIGHT_SCRIPT}</body></html>"


def render_destinations(data: dict) -> str:
    cards = "".join(
        f"<article class='card'><h3>{html.escape(d['name'])}</h3>"
        f"<div class='muted'>{html.escape(d.get('region', ''))} Province</div>"
        f"<p>{html.escape(d.get('description', ''))}</p>"
        f"<ul>{''.join(f'<li>{html.escape(h)}</li>' for h in d.get('highlights', []))}</ul></article>"
        for d in data.get("destinations", [])
    )
    return page(f"<div class='grid'>{cards}</div>")


# WMO weather codes used by Open-Meteo (https://open-meteo.com/en/docs)
WEATHER_CODES = {0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast", 45: "Fog", 48: "Fog",
                 51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle", 61: "Light rain", 63: "Rain",
                 65: "Heavy rain", 80: "Rain showers", 81: "Rain showers", 82: "Violent rain showers",
                 95: "Thunderstorm", 96: "Thunderstorm with hail", 99: "Thunderstorm with hail"}


def render_live_weather(data: dict) -> str:
    # Artifact = the full Open-Meteo response ("artifact": true, no response_path)
    current, units = data.get("current", {}), data.get("current_units", {})

    def reading(key):
        return html.escape(f"{current.get(key, '?')} {units.get(key, '')}".strip())

    location = f"{data.get('latitude', 0):.2f}, {data.get('longitude', 0):.2f} · {data.get('timezone', '')}"
    return page(
        f"<div class='grid'><article class='card'><div class='muted'>Live weather · {html.escape(location)}</div>"
        f"<div class='big'>{reading('temperature_2m')}</div>"
        f"<div>{html.escape(WEATHER_CODES.get(current.get('weather_code'), 'Unknown conditions'))}</div>"
        f"<div class='muted'>Humidity {reading('relative_humidity_2m')} · Wind {reading('wind_speed_10m')}"
        f" · {html.escape(str(current.get('time', '')))}</div></article></div>"
    )


def render_recipes(recipes: list) -> str:
    # Artifact = the "recipes" list from DummyJSON ("artifact": "recipes")
    cards = []
    for r in recipes:
        image = str(r.get("image", ""))
        img = f"<img src='{html.escape(image)}' alt=''>" if image.startswith("https://") else ""
        minutes = (r.get("prepTimeMinutes") or 0) + (r.get("cookTimeMinutes") or 0)
        cards.append(
            f"<article class='card'>{img}<h3>{html.escape(str(r.get('name', '')))}</h3>"
            f"<div class='muted'>{html.escape(str(r.get('cuisine', '')))} · {html.escape(str(r.get('difficulty', '')))}</div>"
            f"<div>★ {html.escape(str(r.get('rating', '-')))} · {minutes} min</div></article>"
        )
    return page(f"<div class='grid'>{''.join(cards)}</div>")


def render_trip_notes(data: dict) -> str:
    # Artifact = the memory MCP server's structuredContent: {"entities": [...], "relations": [...]}
    cards = "".join(
        f"<article class='card'><h3>{html.escape(str(e.get('name', '')))}</h3>"
        f"<div class='muted'>{html.escape(str(e.get('entityType', '')))}</div>"
        f"<ul>{''.join(f'<li>{html.escape(str(o))}</li>' for o in e.get('observations', []))}</ul></article>"
        for e in data.get("entities", [])
    )
    if not cards:
        return ""
    relations = "".join(
        f"<li>{html.escape(str(r.get('from', '')))} → {html.escape(str(r.get('relationType', '')))}"
        f" → {html.escape(str(r.get('to', '')))}</li>"
        for r in data.get("relations", [])
    )
    links = f"<ul class='muted'>{relations}</ul>" if relations else ""
    return page(f"<div class='muted'>📒 Trip notes</div><div class='grid'>{cards}</div>{links}")


def render_added_notes(data: dict) -> str:
    # Artifact of add_observations: {"results": [{"entityName", "addedObservations"}]}
    return render_trip_notes({"entities": [
        {"name": r.get("entityName", ""), "entityType": "added to notes", "observations": r.get("addedObservations", [])}
        for r in data.get("results", []) if r.get("addedObservations")
    ]})


# Tool name -> HTML renderer. Tools without a renderer still work; they just show no card.
# search_destinations is a local @tool in server.py; get_live_weather and search_recipes are HTTP tools
# and the trip-notes tools come from the memory MCP server (all in agent_config.json).
RENDERERS = {
    "search_destinations": render_destinations,
    "get_live_weather": render_live_weather,
    "search_recipes": render_recipes,
    "create_entities": render_trip_notes,
    "search_nodes": render_trip_notes,
    "read_graph": render_trip_notes,
    "add_observations": render_added_notes,
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
                            card = renderer(data["data"]) if renderer and data["status"] == "success" and data["data"] else ""
                            if card:  # skip None and empty results
                                embeds[data["call_id"]] = card
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
