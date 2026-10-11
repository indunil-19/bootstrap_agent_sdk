"""Backend of the custom React UI: the FakeStore agent's stream_chat() events, sent as-is over SSE.

Run from the examples folder:
    python -m custom_ui.server        # http://localhost:8001
Then start the React app in custom_ui/web (see custom_ui/README.md).
"""

import json
import logging
import os
from contextlib import aclosing, asynccontextmanager

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from fakestore import DEMO_USER_ID, create_store_agent

load_dotenv()
logging.basicConfig(level=logging.INFO)


class ChatRequest(BaseModel):
    message: str
    chat_id: str
    chat_history: list[dict] = Field(default_factory=list)  # [{"role": "user" | "assistant", "content": "..."}]


def encode_sse(event: dict) -> str:
    body = json.dumps(event, ensure_ascii=False, allow_nan=False)
    return f"id: {event['run_id']}:{event['seq']}\nevent: {event['type']}\ndata: {body}\n\n"


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.agent = await create_store_agent("custom-ui-store-agent")
    yield


app = FastAPI(lifespan=lifespan)


@app.post("/api/chat/stream")
async def chat_stream(body: ChatRequest, request: Request):
    agent = request.app.state.agent
    # The signed-in user goes in the payload on the server (get_my_cart reads it); the browser can't change it
    payload = {"user_id": DEMO_USER_ID}

    async def generate():
        async with aclosing(agent.stream_chat(body.message, body.chat_id, payload=payload,
                                              chat_history=body.chat_history)) as events:
            async for event in events:
                if await request.is_disconnected():
                    break
                yield encode_sse(event)

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# After "npm run build", the backend also serves the built app, so one URL is enough
WEB_DIST = os.path.join(os.path.dirname(__file__), "web", "dist")
if os.path.isdir(WEB_DIST):
    app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8001")))
