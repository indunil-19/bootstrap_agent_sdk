from langgraph.graph import MessagesState


class State(MessagesState):
    summary: str
    completed: bool
    payload: dict
    input: str
    plan: list[str]
    original_plan: list[str]
    past_steps: list[str]
    response: str
    question: bool
    final_response: bool
    response_instructions : str
    intent:str
    scenario:str
    action : list[str]
    agent_instructions : str
    tools : str