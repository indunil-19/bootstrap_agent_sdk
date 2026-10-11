from langgraph.graph import MessagesState


class State(MessagesState):
    summary: str
    payload: dict
    intent: str
    # Artifacts of the tools called in the current turn: [{"call_id", "name", "data"}]
    tool_outputs: list[dict]
