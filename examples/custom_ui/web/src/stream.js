// Reads the backend's stream of ChatAgent events (SSE over POST, so EventSource can't be used) and calls
// onEvent(event) for each {"schema_version", "run_id", "thread_id", "seq", "type", "data"}.
export async function streamChat(body, onEvent, signal) {
  const response = await fetch("/api/chat/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(body),
    signal,
  });
  if (!response.ok) throw new Error(`The backend returned HTTP ${response.status}`);

  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) return;
    buffer += value;
    // SSE frames end with a blank line; only the "data:" lines carry the JSON event
    const frames = buffer.split("\n\n");
    buffer = frames.pop();
    for (const frame of frames) {
      const data = frame.split("\n").filter((line) => line.startsWith("data:")).map((line) => line.slice(5).trimStart()).join("\n");
      if (!data) continue;
      const event = JSON.parse(data);
      if (event.schema_version === "1") onEvent(event);
    }
  }
}
