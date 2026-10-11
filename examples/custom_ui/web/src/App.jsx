import { useEffect, useRef, useState } from "react";
import { ReplyText, SUGGESTIONS, TOOL_LABELS, ToolCard } from "./cards";
import { streamChat } from "./stream";

const chatId = crypto.randomUUID();

// One turn = the user's message, the tools that ran (with their data), the reply text and token usage
export default function App() {
  const [turns, setTurns] = useState([]);
  const [busy, setBusy] = useState(false);
  const [input, setInput] = useState("");
  const endRef = useRef(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [turns]);

  const update = (id, change) => setTurns((all) => all.map((turn) => (turn.id === id ? { ...turn, ...change(turn) } : turn)));

  async function ask(message) {
    if (!message.trim() || busy) return;
    const id = crypto.randomUUID();
    // The app owns the conversation: earlier messages go to the backend as chat_history on every turn
    const chatHistory = turns.flatMap((turn) => [
      { role: "user", content: turn.user },
      ...(turn.text ? [{ role: "assistant", content: turn.text }] : []),
    ]);
    setTurns((all) => [...all, { id, user: message, tools: [], text: "", status: "running" }]);
    setInput("");
    setBusy(true);

    try {
      await streamChat({ message, chat_id: chatId, chat_history: chatHistory }, ({ type, data }) => {
        if (type === "tool.started") {
          update(id, (turn) => ({ tools: [...turn.tools, { ...data, status: "running" }] }));
        } else if (type === "tool.finished") {
          // data.data is the tool's artifact: the card renders as soon as the tool ends
          update(id, (turn) => ({ tools: turn.tools.map((tool) => (tool.call_id === data.call_id ? { ...tool, ...data } : tool)) }));
        } else if (type === "message.delta") {
          update(id, (turn) => ({ text: turn.text + data.text }));
        } else if (type === "run.finished") {
          // The final text replaces the streamed deltas (fixed replies have no deltas)
          update(id, () => ({ text: data.text, usage: data.usage, status: "done" }));
        } else if (type === "run.error") {
          update(id, () => ({ error: data.message, status: "error" }));
        }
      });
      update(id, (turn) => (turn.status === "running" ? { status: "error", error: "The stream ended unexpectedly." } : {}));
    } catch (error) {
      update(id, () => ({ status: "error", error: String(error.message || error) }));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="app">
      <header>
        <h1>
          FakeStore assistant <span className="badge">custom UI · stream_chat events</span>
        </h1>
        <span className="muted">Signed in as user 1</span>
      </header>

      <main>
        {turns.length === 0 && (
          <div className="welcome">
            <p>Ask about products, compare them or check your cart.</p>
            <div className="chips">
              {SUGGESTIONS.map((text) => (
                <button key={text} className="chip" onClick={() => ask(text)}>{text}</button>
              ))}
            </div>
          </div>
        )}

        {turns.map((turn) => (
          <div key={turn.id} style={{ display: "contents" }}>
            <div className="bubble user">{turn.user}</div>
            {turn.tools.length > 0 && (
              <div className="status">
                {turn.tools.map((tool) => (
                  <span key={tool.call_id} className={`pill ${tool.status}`}>
                    {tool.status === "running" ? "⏳" : tool.status === "error" ? "⚠️" : "✓"} {TOOL_LABELS[tool.name] || tool.name}
                  </span>
                ))}
              </div>
            )}
            {turn.tools.map((tool) =>
              tool.status === "success" ? <ToolCard key={tool.call_id} name={tool.name} data={tool.data} onAsk={ask} /> : null,
            )}
            {turn.text ? <ReplyText text={turn.text} /> : turn.status === "running" && <div className="typing">Thinking…</div>}
            {turn.error && <div className="error">⚠️ {turn.error}</div>}
            {turn.usage && (
              <div className="usage">
                {turn.usage.total_tokens.toLocaleString()} tokens ·{" "}
                {Object.entries(turn.usage.by_step).map(([step, usage]) => `${step} ${usage.total_tokens.toLocaleString()}`).join(" · ")}
              </div>
            )}
          </div>
        ))}
        <div ref={endRef} />
      </main>

      <footer>
        <form onSubmit={(e) => { e.preventDefault(); ask(input); }}>
          <input value={input} onChange={(e) => setInput(e.target.value)} placeholder="Ask about products…" disabled={busy} />
          <button className="primary" disabled={busy || !input.trim()}>Send</button>
        </form>
      </footer>
    </div>
  );
}
