import logging
import time

from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage, AIMessage, RemoveMessage, SystemMessage, ToolMessage
from langgraph.constants import START, END
from langgraph.graph import StateGraph
from langgraph.prebuilt import create_react_agent

from net.kairachat.agent.agent import Agent
from net.kairachat.agent.config.Agent_config import AgentConfig
from net.kairachat.agent.models.State import State

logger = logging.getLogger(__name__)

# Once history exceeds SUMMARY_TRIGGER messages, the oldest SUMMARY_TRIGGER messages are
# summarized and removed; the rest are kept until history exceeds SUMMARY_TRIGGER again.
SUMMARY_TRIGGER = 20

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
        logger.info("[%s] agent: running ReAct agent with %d messages", self.name, len(state["messages"]))
        start = time.perf_counter()

        agent_response = await self.agent.ainvoke(state)

        # Log the intermediate ReAct steps (tool calls and tool results) produced for this query
        for message in agent_response["messages"][len(state["messages"]):]:
            if isinstance(message, AIMessage) and message.tool_calls:
                for tool_call in message.tool_calls:
                    logger.info("[%s] agent: tool call -> %s(%s)", self.name, tool_call["name"], tool_call["args"])
            elif isinstance(message, ToolMessage):
                logger.info("[%s] agent: tool result <- %s: %s", self.name, message.name, message.content)

        final_message = agent_response["messages"][-1]
        logger.info("[%s] agent: done in %.2fs", self.name, time.perf_counter() - start)

        return {"messages": [final_message]}


    async def summarizer_node(self, state:State):
        latest_messages = state["messages"][:SUMMARY_TRIGGER]
        logger.info("[%s] summarize_conversation: summarizing %d older messages", self.name, len(latest_messages))
        start = time.perf_counter()

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
        logger.info("[%s] summarize_conversation: done in %.2fs, removed %d messages", self.name, time.perf_counter() - start, len(latest_messages))
        logger.debug("[%s] summarize_conversation: new summary:\n%s", self.name, response.content)

        return {"summary": response.content, "messages": [
            RemoveMessage(id=message.id)
            for message in latest_messages
        ],}

    async def response_node(self, state: State):
        summary = state.get("summary", "")
        messages = state.get("messages", [])
        logger.info("[%s] response_generator: generating final response", self.name)
        start = time.perf_counter()

        # Inject summary as system context so it is not lost.
        llm_input = messages
        if summary:
            llm_input = [SystemMessage(content=f"Conversation summary:\n{summary}")] + messages

        response = await self.llm.ainvoke(llm_input)
        logger.info("[%s] response_generator: done in %.2fs", self.name, time.perf_counter() - start)
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
        if len(messages) > SUMMARY_TRIGGER:
            logger.info("[%s] route: %d messages > %d -> summarize_conversation", self.name, len(messages), SUMMARY_TRIGGER)
            return "summarize_conversation"
        else:
            logger.info("[%s] route: %d messages -> agent", self.name, len(messages))
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
        logger.info("[%s] query received: %s", self.name, message)
        start = time.perf_counter()

        result = await self.graph.ainvoke(state)
        logger.info("[%s] query completed in %.2fs", self.name, time.perf_counter() - start)

        # Persist the graph output back into the caller's state so that
        # subsequent turns continue from the full (possibly summarized) history.
        state["messages"] = result["messages"]
        if "summary" in result:
            state["summary"] = result["summary"]

        return result["messages"][-1].content


