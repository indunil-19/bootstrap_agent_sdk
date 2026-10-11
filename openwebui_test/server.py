"""Test backend for Open WebUI: a small travel ChatAgent behind /chat and /chat/stream (SSE).

Run from the repo root:
    python -m openwebui_test.server
Then connect Open WebUI with the Pipe in openwebui_test/pipe.py (see openwebui_test/README.md).
"""

import json
import logging
import os
from contextlib import aclosing, asynccontextmanager

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from net.kairachat.agent.llm_agent import ChatAgent

load_dotenv()
logging.basicConfig(level=logging.INFO)

# Demo data; a real application would call its own services here
DESTINATIONS = {
    "ella": {"id": "ella", "name": "Ella", "region": "Uva", "description": "Mountains, tea estates and hiking",
             "highlights": ["Nine Arches Bridge", "Little Adam's Peak", "Ella Rock"]},
    "galle": {"id": "galle", "name": "Galle", "region": "Southern", "description": "Dutch fort and beaches",
              "highlights": ["Galle Fort", "Unawatuna Beach", "Lighthouse"]},
    "kandy": {"id": "kandy", "name": "Kandy", "region": "Central", "description": "Temples and the hill capital",
              "highlights": ["Temple of the Tooth", "Kandy Lake", "Peradeniya Gardens"]},
    "sigiriya": {"id": "sigiriya", "name": "Sigiriya", "region": "Central", "description": "Ancient rock fortress",
                 "highlights": ["Lion Rock", "Frescoes", "Pidurangala Rock"]},
}
WEATHER = {"ella": (19, "Misty"), "galle": (29, "Sunny"), "kandy": (24, "Partly cloudy"), "sigiriya": (31, "Sunny")}


@tool(response_format="content_and_artifact")
async def search_destinations(interest: str = "any"):
    """Search Sri Lankan travel destinations. interest: e.g. hiking, beaches, history, or "any"."""
    words = interest.lower().split()
    items = [d for d in DESTINATIONS.values()
             if interest == "any" or any(w in (d["description"] + " " + " ".join(d["highlights"])).lower() for w in words)]
    items = items or list(DESTINATIONS.values())
    content = "; ".join(f"{d['name']} ({d['description']})" for d in items)
    return f"Found {len(items)} destinations: {content}.", {"destinations": items}


@tool(response_format="content_and_artifact")
async def get_weather(destination: str):
    """Get the current weather for a Sri Lankan destination, e.g. Ella or Galle."""
    key = destination.strip().lower()
    if key not in WEATHER:
        return f"No weather data for {destination}.", None
    temperature, condition = WEATHER[key]
    return (f"Weather in {DESTINATIONS[key]['name']}: {condition}, {temperature} °C.",
            {"destination": DESTINATIONS[key]["name"], "temperature_c": temperature, "condition": condition})


class ChatRequest(BaseModel):
    message: str
    chat_id: str
    chat_history: list[dict] = Field(default_factory=list)


def encode_sse(event: dict) -> str:
    body = json.dumps(event, ensure_ascii=False, allow_nan=False)
    return f"id: {event['run_id']}:{event['seq']}\nevent: {event['type']}\ndata: {body}\n\n"


@asynccontextmanager
async def lifespan(app: FastAPI):
    config_path = os.path.join(os.path.dirname(__file__), "agent_config.json")
    app.state.agent = await ChatAgent.create("openwebui-test-agent", config_path,
                                             tools=[search_destinations, get_weather])
    yield


app = FastAPI(lifespan=lifespan)


@app.post("/chat")
async def chat(body: ChatRequest, request: Request):
    return await request.app.state.agent.chat(body.message, body.chat_id, chat_history=body.chat_history)


@app.post("/chat/stream")
async def chat_stream(body: ChatRequest, request: Request):
    agent = request.app.state.agent

    async def generate():
        async with aclosing(agent.stream_chat(body.message, body.chat_id, chat_history=body.chat_history)) as events:
            async for event in events:
                if await request.is_disconnected():
                    break
                yield encode_sse(event)

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


if __name__ == "__main__":
    # 0.0.0.0 so Open WebUI running in Docker can reach it via host.docker.internal
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
