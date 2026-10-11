import { HttpAgent } from "@ag-ui/client";
import { useEffect, useRef, useState } from "react";
import { ReplyText, SUGGESTIONS, TOOL_LABELS, ToolCard } from "./cards";

// The AG-UI client keeps the conversation (agent.messages) and sends it with every run
const agent = new HttpAgent({ url: "/api/agui", threadId: crypto.randomUUID() });

// Tool results arrive as tool messages; the content is the tool's data as JSON (see agui/adapter.py)
function parseData(content) {
  try {
    return JSON.parse(content);
  } catch {
    return null; // a text-only result: no card
  }
}

export default function App() {
  const [messages, setMessages] = useState([]);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const [usage, setUsage] = useState({}); // last message id of a run -> AG-UI token usage
  const [input, setInput] = useState("");
  const endRef = useRef(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, status]);

  async function ask(text) {
    if (!text.trim() || busy) return;
    agent.addMessage({ id: crypto.randomUUID(), role: "user", content: text });
    setMessages([...agent.messages]);
    setInput("");
    setError("");
    setBusy(true);
    try {
      await agent.runAgent({}, {
        // The client applies every event to agent.messages: tool calls, tool results and the streamed reply
        onMessagesChanged: ({ messages }) => setMessages([...messages]),
        onToolCallStartEvent: ({ event }) => setStatus(`${TOOL_LABELS[event.toolCallName] || event.toolCallName}…`),
        onTextMessageStartEvent: () => setStatus(""),
        onRunFinishedEvent: ({ event, messages }) => {
          if (event.usage?.length) setUsage((all) => ({ ...all, [messages.at(-1).id]: event.usage }));
        },
        onRunErrorEvent: ({ event }) => setError(event.message),
      });
    } catch (e) {
      setError(String(e.message || e));
    } finally {
      setBusy(false);
      setStatus("");
    }
  }

  // Tool name of each tool call, to pick the card for its result
  const toolNames = {};
  for (const message of messages) {
    for (const call of message.toolCalls || []) toolNames[call.id] = call.function.name;
  }

  return (
    <div className="app">
      <header>
        <h1>
          FakeStore assistant <span className="badge">AG-UI · @ag-ui/client</span>
        </h1>
        <span className="muted">Signed in as user 1</span>
      </header>

      <main>
        {messages.length === 0 && (
          <div className="welcome">
            <p>Ask about products, compare them or check your cart.</p>
            <div className="chips">
              {SUGGESTIONS.map((text) => (
                <button key={text} className="chip" onClick={() => ask(text)}>{text}</button>
              ))}
            </div>
          </div>
        )}

        {messages.map((message) => {
          if (message.role === "user") return <div key={message.id} className="bubble user">{message.content}</div>;
          if (message.role === "tool") {
            return <ToolCard key={message.id} name={toolNames[message.toolCallId]} data={parseData(message.content)} onAsk={ask} />;
          }
          if (message.role === "assistant" && message.content) {
            return (
              <div key={message.id} style={{ display: "contents" }}>
                <ReplyText text={message.content} />
                {usage[message.id] && (
                  <div className="usage">
                    {usage[message.id].map((u) => `${u.totalTokens.toLocaleString()} tokens (${u.model})`).join(" · ")}
                  </div>
                )}
              </div>
            );
          }
          return null; // assistant messages that only hold tool calls
        })}
        {status && <div className="typing">{status}</div>}
        {busy && !status && messages.at(-1)?.role !== "assistant" && <div className="typing">Thinking…</div>}
        {error && <div className="error">⚠️ {error}</div>}
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
