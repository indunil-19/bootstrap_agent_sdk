# FakeStore assistant: AG-UI

The same [FakeStore](https://fakestoreapi.com/) shopping assistant as the [custom UI example](../custom_ui/README.md), served over the [AG-UI protocol](https://docs.ag-ui.com). A React app talks to it with the official `@ag-ui/client`; any other AG-UI client (for example CopilotKit) can use the same endpoint.

```
React app + @ag-ui/client ──POST /api/agui (RunAgentInput)──► server.py ──► ChatAgent.stream_chat()
          ◄──── AG-UI events (SSE) ◄──── adapter.py (library events → AG-UI events) ◄──
```

The library doesn't know about AG-UI. `adapter.py` (about 100 lines) translates its events, and this is the only AG-UI-specific code on the server.

| File | Role |
|---|---|
| `adapter.py` | `chat_args()`: AG-UI messages → `message` + `chat_history`. `to_agui_events()`: library events → AG-UI events. |
| `server.py` | FastAPI: `POST /api/agui` takes a `RunAgentInput` and streams AG-UI events (`ag-ui-protocol` models and `EventEncoder`); also serves the built app. |
| `web/src/App.jsx` | `HttpAgent` from `@ag-ui/client`; renders `agent.messages`. |
| `web/src/cards.jsx` | Tool name → React component (the same file as in the custom UI example). |

The agent, its tools and the signed-in user (`payload={"user_id": ...}` set on the server) are the same as in the custom UI example; see [its README](../custom_ui/README.md#the-agent-and-its-tools).

## Run it

Requirements: the repo set up as in the [main README](../../README.md) (venv, `pip install -e .`, `.env`), Node.js 20.19+.

**1. Backend** (from the `examples` folder):

```bash
pip install fastapi uvicorn ag-ui-protocol   # once
python -m agui.server                        # http://localhost:8002
```

**2. Frontend**, in a second terminal:

```bash
cd examples/agui/web
npm install                      # once
npm run dev                      # http://localhost:5173, /api is proxied to the backend
```

Or build it once with `npm run build`; the backend then serves the app itself at http://localhost:8002.

Try the same messages as in the [custom UI example](../custom_ui/README.md#try-it); the cards and behaviour are the same.

## Event mapping

| Library event | AG-UI events |
|---|---|
| `run.started` | `RUN_STARTED` (with the client's `threadId` and `runId`) |
| `tool.started` | `TOOL_CALL_START`, `TOOL_CALL_ARGS` (all arguments as one JSON delta), `TOOL_CALL_END` |
| `tool.finished` | `TOOL_CALL_RESULT`: `content` is the tool's data (artifact) as JSON, or its text if it has none |
| `message.delta` | `TEXT_MESSAGE_START` (first delta), `TEXT_MESSAGE_CONTENT` |
| `run.finished` | `TEXT_MESSAGE_END` (or START/CONTENT/END for fixed replies, which have no deltas), `RUN_FINISHED` with `usage` |
| `run.error` | `RUN_ERROR` |

Notes:

- **Tool data:** AG-UI tool results are strings, so the adapter sends the artifact as JSON. The app parses it and picks the card by the tool call's name; a result that isn't JSON (a text-only tool) gets no card.
- **Token usage** goes in AG-UI's own `RUN_FINISHED.usage` (`inputTokens`, `outputTokens`, `totalTokens`, `reasoningTokens`, `cachedInputTokens`, `model`). The per-step breakdown stays on the server (logs, or `chat()`).
- **Tool call ids** are the library's tool invocation ids. They pair `TOOL_CALL_START` with `TOOL_CALL_RESULT` within the stream; they are not the LLM's tool call ids.

## Conversation history

AG-UI clients keep the conversation and send all of it (`messages`) with every run, including tool calls and tool results. `chat_args()` keeps only the user and assistant text: the last user message becomes `message`, the earlier ones become `chat_history`. This matches the library, which doesn't keep tool calls in history. `threadId` is used as the `chat_id`.

## How the app renders

`@ag-ui/client` applies every event to `agent.messages`, so the app only renders that list (updated through `onMessagesChanged`):

| Message | Rendered as |
|---|---|
| `user` | User bubble |
| `assistant` with `toolCalls` only | Nothing (it just gives each tool call's name) |
| `tool` | The card for that tool call's name, from the JSON `content` |
| `assistant` with `content` | Reply bubble, plus the run's token usage |

`onToolCallStartEvent` shows "Searching products…" while a tool runs. Buttons on cards send a normal user message, which starts a new run.

## Not used here

AG-UI also has shared state (`STATE_SNAPSHOT`/`STATE_DELTA`), frontend tools (tools the client defines and runs) and interrupts. This adapter doesn't use them: the agent's tools all run on the server, and `RunAgentInput.tools`, `state` and `context` are ignored.
