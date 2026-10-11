# FakeStore assistant: custom React UI

A shopping assistant for [FakeStore](https://fakestoreapi.com/) (a free fake shop API, no key needed) with a React chat UI that reads the library's own `stream_chat()` events directly. Product cards, a comparison table and the cart appear as soon as each tool finishes, then the reply streams in.

```
React app (web/) ──POST /api/chat/stream──► server.py ──► ChatAgent.stream_chat()
      ◄──── SSE: run.started, tool.started, tool.finished (+ data), message.delta…, run.finished ◄──
```

No protocol adapter: the browser gets the library's events as they are. For a standard protocol, see the [AG-UI example](../agui/README.md); both use the same agent and the same cards.

## The agent and its tools

The agent lives in [`../fakestore`](../fakestore) and is shared with the AG-UI example.

| Tool | Kind | What it does | Card |
|---|---|---|---|
| `search_products` | Local tool (`fakestore/tools.py`) | Keyword search with `max_price`, `min_rating` and sorting. FakeStore has no search endpoint, so it filters the full product list. | Product grid |
| `list_categories` | HTTP tool, `"artifact": true` | `GET /products/categories` | Category chips |
| `get_products_in_category` | HTTP tool, `"artifact": true` | `GET /products/category/{category}` | Product grid |
| `get_product` | HTTP tool, `"artifact": true` | `GET /products/{product_id}` | Product details |
| `compare_products` | Local tool | Several products side by side | Comparison table (best price and rating highlighted) |
| `get_my_cart` | Local tool, reads the payload | The signed-in user's latest cart, with totals | Cart |

The HTTP tools are declared in `fakestore/agent_config.json`. The local tools are the ones the REST API can't do in one request.

**The signed-in user** is set on the server: `server.py` passes `payload={"user_id": DEMO_USER_ID}` to `stream_chat()`, and `get_my_cart` reads it from its `config`. The browser never sends the user id, so it can't ask for someone else's cart. Change the user with `STORE_USER_ID` (FakeStore has users 1–10).

| File | Role |
|---|---|
| `server.py` | FastAPI: `POST /api/chat/stream` sends the `stream_chat()` events as SSE; also serves the built app. |
| `web/src/stream.js` | Reads the SSE stream (with `fetch`, since `EventSource` can't POST). |
| `web/src/App.jsx` | Chat state: one turn = user message, tools (status + data), reply text, token usage. |
| `web/src/cards.jsx` | Tool name → React component (the same file as in the AG-UI example). |

## Run it

Requirements: the repo set up as in the [main README](../../README.md) (venv, `pip install -e .`, `.env`), Node.js 20.19+.

**1. Backend** (from the `examples` folder):

```bash
pip install fastapi uvicorn      # once
python -m custom_ui.server       # http://localhost:8001
```

The log ends with `agent ready with tools: ['search_products', 'compare_products', 'get_my_cart', 'list_categories', 'get_products_in_category', 'get_product']`.

**2. Frontend**, in a second terminal:

```bash
cd examples/custom_ui/web
npm install                      # once
npm run dev                      # http://localhost:5173, /api is proxied to the backend
```

Or build it once with `npm run build`; the backend then serves the app itself at http://localhost:8001.

Use another backend port with `PORT=8011` and start the frontend with `BACKEND_URL=http://localhost:8011`.

## Try it

1. **What categories do you have?** Category chips; click one to browse it.
2. **Find a backpack under $120.** `search_products` with `max_price`; one product card.
3. **Show me jackets sorted by price.** Four jackets, cheapest first. Tick **Compare** on two of them, then click **Compare 2 products**.
4. **Compare the two SSDs.** `search_products` then `compare_products`: the comparison table.
5. Click **Details** on any card: a new turn with `get_product` and the details card.
6. **What's in my cart?** `get_my_cart`: user 1's cart with quantities and the total.
7. **Which of those is the best rated?** Uses the chat history; plain answer.

Each answer ends with its token usage, e.g. `2,797 tokens · agent 2,151 · response_generator 646`.

## How the UI uses the events

| Event | What the UI does |
|---|---|
| `tool.started` | Adds a "⏳ Searching products" pill |
| `tool.finished` | Marks the pill ✓ and renders `data` (the tool's artifact) with the component for the tool's `name` |
| `message.delta` | Appends the text to the reply |
| `run.finished` | **Replaces** the reply with `text` (fixed replies have no deltas) and shows `usage` |
| `run.error` | Shows the error |

Buttons on cards (Details, Compare, a category chip) don't call tools directly: they send a normal message such as "Show me the details of product #10", so the agent stays in charge and the conversation history stays complete.

The app owns the conversation: every request sends the earlier user/assistant messages as `chat_history`, and `chat_id` stays the same for the session.

## Adding a card

1. Make the tool return data for the UI: `@tool(response_format="content_and_artifact")` for a local tool, `"artifact"` for an HTTP tool, nothing for an MCP tool that returns JSON (check with `python -m bootstrap_agent.mcp_ui_check`).
2. Add a component to `RENDERERS` in `web/src/cards.jsx`, keyed by the tool name.

React escapes all text; image URLs are only used if they start with `https://`.
