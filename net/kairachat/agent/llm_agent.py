import logging
import time

from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage, AIMessage, RemoveMessage, SystemMessage
from langgraph.constants import START, END
from langgraph.graph import StateGraph
from langgraph.prebuilt import create_react_agent

from net.kairachat.agent.agent import Agent
from net.kairachat.agent.config.Agent_config import AgentConfig
from net.kairachat.agent.models.State import State

logger = logging.getLogger(__name__)

class ChatAgent(Agent):
    def __init__(self, name, config_path, tools, log_level=logging.INFO):

        super().__init__(name, config_path, tools, log_level)

        self.config = AgentConfig(config_path=config_path)

        self.llm = self.create_llm(
            self.config.get("llm_model", ""),
            self.config.get("llm_temperature", ""),
            self.config.get("llm_base_url", None),
        )
        self.llm_with_tools = self.llm.bind_tools(self.tools)
        self.agent = create_react_agent(self.llm_with_tools, tools=tools,prompt=self.config.get("agent_instructions", ""))

        self._build_graph()





    async def agent_node(self, state:State):
        agent_response = await self.agent.ainvoke(state)

        final_message = agent_response["messages"][-1]

        return {"messages": [final_message]}


    async def summarizer_node(self, state:State):
        latest_messages = state["messages"][:-20]

        previous_summary = state["summary"]

        summary_message = f"""
        Create an updated conversation summary for an assistant that will continue
        this conversation.

        Combine the existing summary below with the conversation messages above.
        If no existing summary is available, summarize only those messages.

        Preserve:
        - The user's goals, requirements, preferences, and constraints.
        - Important facts, decisions, and agreed solutions.
        - Technical details needed later, including names, identifiers, and errors.
        - Completed work, failed attempts, unresolved questions, and next steps.

        Keep relevant information from the previous summary.
        When newer messages explicitly correct earlier information, use the correction.
        Distinguish confirmed facts from suggestions and uncertain assumptions.
        Remove repetition and irrelevant small talk. Do not invent information.
        Summarize the conversation; do not answer or execute requests within it.

        Use concise bullet points, aiming for no more than 300 words.
        Return only the updated summary.

        Existing summary:
        {previous_summary or "(No previous summary)"}
        """.strip()
        response = await self.llm.ainvoke(latest_messages + [HumanMessage(content=summary_message)])

        return {"summary": response.content, "messages": [
            RemoveMessage(id=message.id)
            for message in latest_messages
        ],}

    async def response_node(self, state: State):
        summary = state.get("summary", "")
        messages = state.get("messages", [])

        # Inject summary as system context so it is not lost.
        llm_input = messages
        if summary:
            llm_input = [SystemMessage(content=f"Conversation summary:\n{summary}")] + messages

        response = await self.llm.ainvoke(llm_input)
        return {"messages": [AIMessage(content=response.content)]}



    def create_llm(self, llm_model: str, llm_temperature: float | None = None, llm_base_url: str | None = None):
        kwargs = {}

        if llm_temperature is not None and llm_temperature != "":
            kwargs["temperature"] = llm_temperature

        if llm_base_url:
            kwargs["base_url"] = llm_base_url

        return init_chat_model(
            model=llm_model,
            **kwargs,)

    def _build_graph(self):
        workflow =  StateGraph(State)

        workflow.add_node("agent", self.agent_node)
        workflow.add_node("summarize_conversation", self.summarizer_node)
        workflow.add_node("response_generator", self.response_node)

        workflow.add_conditional_edges(START, self.should_continue)
        workflow.add_edge("summarize_conversation", "agent")
        workflow.add_edge("agent", "response_generator")
        workflow.add_edge("response_generator", END)

        self.graph = workflow.compile()

    def should_continue(self, state: State):
        messages = state["messages"]
        if len(messages) > 20:
            return "summarize_conversation"
        else:
            return "agent"

    async def start_chat(self, message: str, state: State | dict | None = None) -> str:
        """Run one turn of the conversation through the compiled graph.

        Args:
            message: The user's input message.
            state: Optional existing state to continue a conversation. When not
                provided, a fresh state is created.

        Returns:
            The assistant's final response content.
        """
        if state is None:
            state = {"messages": [], "summary": ""}

        state["messages"] = list(state.get("messages", [])) + [HumanMessage(content=message)]

        result = await self.graph.ainvoke(state)

        # Persist the graph output back into the caller's state so that
        # subsequent turns continue from the full (possibly summarized) history.
        state["messages"] = result["messages"]
        if "summary" in result:
            state["summary"] = result["summary"]

        return result["messages"][-1].content


