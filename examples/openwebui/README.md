# Testing ChatAgent with Open WebUI

A small Sri Lanka travel agent behind an SSE endpoint, plus an Open WebUI Pipe that streams its replies and renders the tool artifacts as HTML cards.

It has one tool of each kind that can produce cards:

| Tool | Kind | Data | Card |
|---|---|---|---|
| `search_destinations` | Local `@tool(response_format="content_and_artifact")` in `server.py` | Fixed demo data, including each destination's coordinates | Destination cards |
| `get_live_weather` | HTTP tool in `agent_config.json`, `"artifact": true` | Live, from [Open-Meteo](https://open-meteo.com/) (no API key) | Temperature, conditions, humidity, wind |
| `search_recipes` | HTTP tool in `agent_config.json`, `"artifact": "recipes"` | Fake demo data from [DummyJSON](https://dummyjson.com/docs/recipes) (no API key) | Recipe cards with photos |
| `create_entities`, `add_observations`, `search_nodes`, `read_graph` | MCP tools from the open-source [memory server](https://github.com/modelcontextprotocol/servers/tree/main/src/memory) (`@modelcontextprotocol/server-memory`), no config needed for cards | The user's trip notes, saved in `openwebui_test/trip_notes.jsonl` | Trip-note cards (place or plan, type, notes) |

The agent chains them: for "weather in Ella" it gets Ella's coordinates from `search_destinations`, then calls `get_live_weather` with them.

```
Open WebUI ──► Pipe (pipe.py) ──POST /chat/stream──► server.py ──► ChatAgent.stream_chat()
            ◄── text chunks + "embeds" (cards) ◄──── SSE events ◄──
```

| File | Role |
|---|---|
| `server.py` | FastAPI app: `POST /chat` (JSON) and `POST /chat/stream` (SSE), plus the local `search_destinations` tool. |
| `agent_config.json` | Agent config with the two HTTP tools and the `memory` MCP server. |
| `pipe.py` | Open WebUI Pipe Function. Self-contained (only `httpx` + `pydantic`); holds the tool name → HTML renderer map. |
| `MCP.md` | Guide to the MCP tool cards (memory server). |

## 1. Start the backend

From the repo root, with the venv active and `.env` filled in:

```bash
pip install fastapi uvicorn      # once; the memory MCP server also needs Node.js (npx)
python -m openwebui.server  # listens on 0.0.0.0:8000
```

The log should end with `agent ready with tools: ['search_destinations', 'get_live_weather', 'search_recipes', 'create_entities', 'add_observations', 'search_nodes', 'read_graph']`. Use another port with `PORT=8001` (set `AGENT_URL` in the Pipe's Valves to match).

After changing `server.py` or `agent_config.json`, stop the backend (Ctrl+C) and start it again; a backend that is still running keeps the old tools.

Quick check without Open WebUI:

```bash
curl -N -X POST http://localhost:8000/chat/stream -H "Content-Type: application/json" \
     -d '{"message": "Suggest places for hiking", "chat_id": "t1"}'
```

You should see `run.started`, `tool.started`, `tool.finished` (with the destinations), many `message.delta`, then `run.finished`.

## 2. Start Open WebUI

With Docker (start Docker Desktop first):

```bash
docker run -d -p 3000:8080 --add-host=host.docker.internal:host-gateway \
  -v open-webui:/app/backend/data --name open-webui ghcr.io/open-webui/open-webui:main
```

Open http://localhost:3000 and create the admin account.

## 3. Add the Pipe

1. **Admin Panel → Functions → +** (new function).
2. Paste the whole content of `pipe.py`, give it an id (e.g. `chat_agent`) and save.
3. Enable the function (toggle).
4. Click the function's **Valves** (gear) and check `AGENT_URL`:
   - Open WebUI in Docker: `http://host.docker.internal:8000` (default)
   - Open WebUI installed with pip on the same machine: `http://localhost:8000`
5. Start a new chat and pick **ChatAgent (streaming + cards)** in the model selector.

## 4. Try it

Send these one at a time:

1. **Suggest some places for hiking**
   Status "Running search_destinations…", an Ella destination card, then the reply streams in.
2. **What's the weather in Ella right now?**
   `search_destinations` then `get_live_weather` (live HTTP call). A weather card with the current temperature, conditions, humidity and wind.
3. **And in Galle?**
   Uses the chat history: a live weather card for Galle.
4. **Any curry recipes I could try?**
   `search_recipes` (HTTP call to DummyJSON). Recipe cards with photos, rating and total time.
5. **Show me some biryani or dosa recipes**
   More recipe cards. DummyJSON searches recipe names, so dish names work best.
6. **Save Ella to my trip notes: I want to hike Little Adam's Peak at sunrise**
   `search_nodes` then `create_entities` (MCP memory server). A trip-note card for what was saved.
7. **Also note that I want to take the train from Kandy to Ella**
   Another trip-note card (`create_entities`, or `add_observations` if the place is already in the notes).
8. **Show my trip notes**
   `read_graph`. One card per saved place or plan.
9. **Hello**
   Plain reply, no card.

Reload the page afterwards: the text and the cards should still be there.

## How HTTP tool cards work: `response_path` and `artifact`

An HTTP tool's JSON response is used twice: once for the model and once for the UI. Two config keys control each copy:

```
API JSON response
   ├── response_path ──► what the MODEL reads (text, cut off at max_response_chars)
   └── artifact ──────► what the UI gets (structured JSON, never cut off) → cards
```

### `response_path`: what the model sees

A dotted path into the JSON response. Only that part goes to the model, as text. Use it to drop noise so the model gets only what it needs to answer.

- Not set: the model gets the whole response.
- `"recipes"`: the model gets only the `recipes` list.

It has nothing to do with the UI; it existed before rich UI support.

### `artifact`: what the frontend gets

Decides whether the UI gets data for a card, and which part.

| Value | Frontend gets |
|---|---|
| not set / `false` | Nothing. No card. This is the default. |
| `true` | Exactly what the model got: the `response_path` part, or the whole response if there's no `response_path`. |
| `"some.path"` | That path, read from the **full** response. `response_path` doesn't affect it. |

### Why both?

The model and the UI often need different parts of the response. Take this Open-Meteo response:

```json
{
  "latitude": 6.85, "timezone": "Asia/Colombo",
  "current_units": {"temperature_2m": "°C", "wind_speed_10m": "km/h"},
  "current":       {"temperature_2m": 23.2, "wind_speed_10m": 4.2}
}
```

| Config | Model sees | Card gets |
|---|---|---|
| `"response_path": "current"` | `current` only | nothing |
| `"response_path": "current", "artifact": true` | `current` | `current` (same data) |
| `"response_path": "current", "artifact": "current_units"` | `current` | `current_units` (different part) |
| `"artifact": true` (no `response_path`) | everything | everything |

In this test config:

- `get_live_weather` uses the last row, so both the model and the card get the units. With `response_path: "current"`, the units were cut off and the model guessed wrong ("8 m/s" instead of km/h).
- `search_recipes` uses `"response_path": "recipes"` and `"artifact": "recipes"`, which select the same part; `"artifact": true` would do the same.

### Why the UI data is opt-in and selectable

- **Security:** API responses can contain fields that shouldn't reach the browser, such as internal IDs or other users' data. Without `artifact`, nothing reaches the browser; with a path, only that slice does.
- **No cut-off:** the model's text is limited to `max_response_chars` (8000 by default) to save tokens. The card data isn't limited, so a list of 50 items still renders completely.
- **Errors:** on an HTTP error, a timeout, a missing payload value, a non-JSON response or a missing path, the UI gets `null`, so you never get a broken card.

In short, `response_path` shapes what the model reads, and `artifact` decides what the user sees as a card. The card renderer is looked up by the tool's name in the config (`RENDERERS` in `pipe.py`).

## How MCP tool cards work

MCP tools need no `artifact` setting. When an MCP tool's result includes JSON (`structuredContent`), the agent sends that JSON to the UI as the tool's data; text-only MCP tools give no card. `render_trip_notes` in `pipe.py` turns the memory server's JSON into trip-note cards.

See **[MCP.md](../../docs/MCP.md)** for the full guide: the memory server setup, test messages, the JSON each tool returns, checking a server with `mcp_ui_check` before writing a card, and adding a card for another (or your own) MCP server.

## Notes

- **Trip notes** are kept in `openwebui_test/trip_notes.jsonl` (git-ignored; set `TRIP_NOTES_FILE` to use another file). Delete it to start with empty notes. They are shared by all chats. More in [MCP.md](../../docs/MCP.md#notes).
- **Weather location:** Open-Meteo returns only coordinates, so the weather card shows latitude/longitude and timezone, not the place name.
- **Recipe photos** load from `cdn.dummyjson.com` inside the card's iframe; the renderer only accepts `https://` image URLs.
- **Card height:** each card includes the documented `iframe:height` script, since Open WebUI's iframe sandbox (Allow Same Origin off by default) cannot measure the content itself.
- **Title/tag generation:** Open WebUI also sends background tasks (chat title, tags, follow-ups) to the selected model. The Pipe returns an empty reply for those (`__task__`) instead of running the agent, so chats keep the default title. To get titles, set a different **Task Model** in Admin Panel → Settings → Interface.
- **History:** Open WebUI sends the whole conversation on each request; the Pipe passes all but the last message as `chat_history`, and Open WebUI's chat id as `chat_id`.
- **Adding a card for another tool:** make the tool return `(content, artifact)` with `@tool(response_format="content_and_artifact")` in `server.py` (or, for an HTTP tool in `agent_config.json`, set `"artifact"`; an MCP tool needs nothing if it returns JSON, check with `python -m bootstrap_agent.mcp_ui_check <config>`), then add a renderer for its name to `RENDERERS` in `pipe.py` and re-save the function. Always `html.escape` values.
- Library side: see [docs/rich-ui-streaming.md](../../docs/rich-ui-streaming.md).
