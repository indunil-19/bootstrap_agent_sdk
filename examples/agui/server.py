"""AG-UI endpoint for the FakeStore agent: POST /api/agui takes a RunAgentInput and streams AG-UI events.

Run from the examples folder:
    python -m agui.server             # http://localhost:8002
Then start the React app in agui/web (see agui/README.md). Any AG-UI client can use the endpoint.
"""

import logging
import os
from contextlib import aclosing, asynccontextmanager

import uvicorn
from ag_ui.core import RunAgentInput, RunErrorEvent
from ag_ui.encoder import EventEncoder
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from agui.adapter import chat_args, to_agui_events
from fakestore import DEMO_USER_ID, create_store_agent

load_dotenv()
logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.agent = await create_store_agent("agui-store-agent")
    yield


app = FastAPI(lifespan=lifespan)


@app.post("/api/agui")
async def run_agent(run_input: RunAgentInput, request: Request):
    agent = request.app.state.agent
    encoder = EventEncoder(accept=request.headers.get("accept"))

    async def generate():
        try:
            message, history = chat_args(run_input)
        except ValueError as e:
            yield encoder.encode(RunErrorEvent(message=str(e), code="invalid_input"))
            return
        # The signed-in user goes in the payload on the server (get_my_cart reads it); the browser can't change it
        events = agent.stream_chat(message, run_input.thread_id, payload={"user_id": DEMO_USER_ID},
                                   chat_history=history)
        async with aclosing(events):
            async for event in to_agui_events(events, run_input, model=agent.config.get("llm_model")):
                if await request.is_disconnected():
                    break
                yield encoder.encode(event)

    return StreamingResponse(generate(), media_type=encoder.get_content_type(),
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# After "npm run build", the backend also serves the built app, so one URL is enough
WEB_DIST = os.path.join(os.path.dirname(__file__), "web", "dist")
if os.path.isdir(WEB_DIST):
    app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8002")))
