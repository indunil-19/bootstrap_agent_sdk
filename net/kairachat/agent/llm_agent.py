import logging
import time

from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage, AIMessage, RemoveMessage, SystemMessage, ToolMessage, convert_to_messages
from langchain_core.runnables import RunnableConfig
from langgraph.constants import START, END
from langgraph.graph import StateGraph
from langgraph.prebuilt import create_react_agent

from net.kairachat.agent.agent import Agent
from net.kairachat.agent.config.Agent_config import AgentConfig
from net.kairachat.agent.http_tools import build_http_tools
from net.kairachat.agent.intents import (DEFAULT_HISTORY_MESSAGES, NO_INTENT, build_classifier_prompt,
                                         build_intent_model, build_refusal_prompt, load_intent_settings)
from net.kairachat.agent.mcp_loader import load_mcp_tools_from_config
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

        # HTTP tools need no network at build time, so they load here for both ChatAgent(...) and create()
        self.tools = list(self.tools or []) + build_http_tools(self.config.get("http_tools"))

        self._build_agent()

    @classmethod
    async def create(cls, name, config_path, tools=None, log_level=logging.INFO):
        """Create a ChatAgent with the given local tools plus the MCP tools declared
        under "mcp_servers" in the agent config."""
        agent = cls(name, config_path, list(tools or []), log_level)

        mcp_tools = await load_mcp_tools_from_config(agent.config.get("mcp_servers"))
        if mcp_tools:
            agent.tools = agent.tools + mcp_tools
            agent._build_agent()

        return agent

    def _build_agent(self):
        tool_names = [getattr(tool, "name", None) or tool.__name__ for tool in self.tools]
        duplicates = sorted({tool_name for tool_name in tool_names if tool_names.count(tool_name) > 1})
        if duplicates:
            raise ValueError(f"Duplicate tool names: {duplicates}. Tool names must be unique across local, HTTP and MCP tools.")

        agent_instructions = self.config.get("agent_instructions", "")
        self.llm_with_tools = self.llm.bind_tools(self.tools)
        self.agent = create_react_agent(self.llm_with_tools, tools=self.tools, prompt=agent_instructions)

        # Intent classification: one ReAct agent per "agent"-route intent, with only that intent's tools
        # and instructions. Messages classified as "none" use the full agent above.
        self.intent_settings = load_intent_settings(self.config.get("intent_classification"), tool_names)
        self.intents = {}
        self.intent_agents = {}
        if self.intent_settings:
            self.intents = self.intent_settings["intents"]
            self.intent_classifier = self.llm.with_structured_output(build_intent_model(list(self.intents)))

            for intent_name, intent in self.intents.items():
                if intent.get("route", "agent") != "agent":
                    continue
                intent_tools = self.tools
                if "tools" in intent:
                    intent_tools = [tool for tool, tool_name in zip(self.tools, tool_names) if tool_name in intent["tools"]]
                prompt = agent_instructions
                if intent.get("instructions"):
                    prompt = f"{prompt}\n\n{intent['instructions']}".strip()
                llm = self.llm.bind_tools(intent_tools) if intent_tools else self.llm
                self.intent_agents[intent_name] = create_react_agent(llm, tools=intent_tools, prompt=prompt)

            logger.info("[%s] intent classification enabled: %s", self.name,
                        {name: intent.get("route", "agent") for name, intent in self.intents.items()})

        self._build_graph()
        logger.info("[%s] agent ready with tools: %s", self.name, tool_names)


    async def agent_node(self, state:State, config: RunnableConfig):
        intent = state.get("intent")
        agent = self.intent_agents.get(intent, self.agent)
        logger.info("[%s] agent: running ReAct agent with %d messages", self.name, len(state["messages"]))
        start = time.perf_counter()

        # Make the intent available to tools through the payload
        payload = config.get("configurable", {}).get("payload")
        if intent and payload is not None:
            payload["intent"] = intent

        # Pass the run config through so tools receive the payload and the recursion limit applies
        agent_response = await agent.ainvoke(state, config=config)

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

        user_query = next((m.content for m in reversed(messages) if isinstance(m, HumanMessage)), "")

        system_prompt = f"""
        You are writing the final reply to the user's latest message.

        The user's latest message is:
        "{user_query}"

        The messages that follow this instruction are the newer messages of the conversation. Earlier
        messages are no longer shown and are covered by the conversation summary below.

        The LAST message in the conversation is a DRAFT ANSWER written by an assistant with tool access.
        It is not from the user, so do not reply to it, thank it, or react to it.
        Rewrite the draft into the final reply to the user's latest message:
        - Answer exactly what the user asked, using the facts in the draft answer.
        - The draft reflects real tool results. Keep every fact, number and name exactly as in the draft.
          Do not contradict it, and do not say you cannot access information it provides.
        - Do not add facts that are not in the draft, the messages or the summary.
        - If the draft says something could not be found or done, tell the user that.

        Conversation summary so far:
        {summary or "(No summary yet)"}
        """.strip()

        llm_input = [SystemMessage(content=self._add_response_instructions(system_prompt))] + messages

        response = await self.llm.ainvoke(llm_input)
        logger.info("[%s] response_generator: done in %.2fs", self.name, time.perf_counter() - start)
        return {"messages": [AIMessage(content=response.content)]}

    async def classify_intent_node(self, state: State):
        history_messages = self.intent_settings.get("history_messages", DEFAULT_HISTORY_MESSAGES)
        messages = state["messages"][-(history_messages + 1):]
        start = time.perf_counter()

        system_prompt = build_classifier_prompt(self.intents, state.get("summary", ""))
        try:
            result = await self.intent_classifier.ainvoke([SystemMessage(content=system_prompt)] + messages)
            intent, reason = result.intent, result.reason
        except Exception as e:
            # A failed classification must not break the chat; fall back to the normal agent
            logger.warning("[%s] classify_intent: failed (%s: %s), using '%s'", self.name, type(e).__name__, e, NO_INTENT)
            intent, reason = NO_INTENT, "classification failed"

        route = self.intents.get(intent, {}).get("route", "agent")
        if route != "agent":
            tools = "no tools"
        elif "tools" in self.intents.get(intent, {}):
            tools = ", ".join(self.intents[intent]["tools"])
        else:
            tools = "all tools"
        logger.info("[%s] classify_intent: %s -> %s [%s] in %.2fs (reason: %s)", self.name, intent, route,
                    tools, time.perf_counter() - start, reason)
        return {"intent": intent}

    async def direct_response_node(self, state: State):
        intent = self.intents[state["intent"]]
        logger.info("[%s] direct_response: replying without tools", self.name)

        if intent.get("message"):
            return {"messages": [AIMessage(content=intent["message"])]}

        start = time.perf_counter()
        system_prompt = f"""
        Reply to the user's latest message directly. You have no tools in this step.

        Below is the summary of the conversation so far, covering earlier messages that are no longer
        shown. The messages that follow this instruction are the newer messages of the conversation.

        Conversation summary so far:
        {state.get("summary", "") or "(No summary yet)"}
        """.strip()
        if intent.get("instructions"):
            system_prompt += f"\n\n{intent['instructions']}"

        llm_input = [SystemMessage(content=self._add_response_instructions(system_prompt))] + state["messages"]
        response = await self.llm.ainvoke(llm_input)
        logger.info("[%s] direct_response: done in %.2fs", self.name, time.perf_counter() - start)
        return {"messages": [AIMessage(content=response.content)]}

    async def refuse_node(self, state: State):
        intent = self.intents[state["intent"]]
        logger.info("[%s] refuse: declining the request", self.name)

        if intent.get("message"):
            return {"messages": [AIMessage(content=intent["message"])]}

        start = time.perf_counter()
        system_prompt = self._add_response_instructions(build_refusal_prompt(self.intents))
        # Only the latest message is sent, so the model has nothing else to answer
        response = await self.llm.ainvoke([SystemMessage(content=system_prompt), state["messages"][-1]])
        logger.info("[%s] refuse: done in %.2fs", self.name, time.perf_counter() - start)
        return {"messages": [AIMessage(content=response.content)]}

    def _add_response_instructions(self, system_prompt: str) -> str:
        # Optional deployment-specific instructions from the agent config.
        response_instructions = self.config.get("response_instructions")
        if response_instructions:
            system_prompt += ("\n\n**Response style instructions** (these only change the tone, length and format "
                              "of your reply, never its facts or what it answers):\n"
                              f"{response_instructions}")
        return system_prompt



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

        if self.intent_settings:
            workflow.add_node("classify_intent", self.classify_intent_node)
            workflow.add_node("direct_response", self.direct_response_node)
            workflow.add_node("refuse", self.refuse_node)
            workflow.add_conditional_edges("classify_intent", self.route_by_intent)
            workflow.add_edge("direct_response", END)
            workflow.add_edge("refuse", END)

        workflow.add_conditional_edges(START, self.should_continue)
        workflow.add_edge("summarize_conversation", self._first_step())
        workflow.add_edge("agent", "response_generator")
        workflow.add_edge("response_generator", END)

        self.graph = workflow.compile()

    def _first_step(self):
        return "classify_intent" if self.intent_settings else "agent"

    def should_continue(self, state: State):
        messages = state["messages"]
        if len(messages) > SUMMARY_TRIGGER:
            logger.info("[%s] route: %d messages > %d -> summarize_conversation", self.name, len(messages), SUMMARY_TRIGGER)
            return "summarize_conversation"
        else:
            logger.info("[%s] route: %d messages -> %s", self.name, len(messages), self._first_step())
            return self._first_step()

    def route_by_intent(self, state: State):
        route = self.intents.get(state["intent"], {}).get("route", "agent")
        return {"agent": "agent", "direct": "direct_response", "refuse": "refuse"}[route]

    async def start_chat(self, message: str, chat_id: str, payload: dict | None = None,
                         chat_history: list | None = None) -> str:
        """Run one turn of the conversation through the compiled graph.

        Args:
            message: The user's input message.
            chat_id: Identifier of the conversation, passed as the LangGraph thread_id.
            payload: Optional caller data, passed to nodes and tools via
                config["configurable"]["payload"]. The converted chat_history is added
                to it under the "chat_history" key.
            chat_history: Optional previous messages of the conversation, as LangChain
                messages or dicts like {"role": "user", "content": "..."}.

        Returns:
            The assistant's final response content.
        """
        history = convert_to_messages(chat_history or [])
        # Copy so the caller's payload is not mutated; tools can read the history via the payload too
        payload = {**(payload or {}), "chat_history": history}
        state = {
            "messages": history + [HumanMessage(content=message)],
            "summary": "",
            "payload": payload,
        }
        config = {
            "configurable": {"thread_id": chat_id, "payload": payload},
            "recursion_limit": self.config.get("recursion_limit", 50),
        }
        logger.info("[%s] [chat %s] query received: %s", self.name, chat_id, message)
        start = time.perf_counter()

        result = await self.graph.ainvoke(state, config=config)
        logger.info("[%s] [chat %s] query completed in %.2fs", self.name, chat_id, time.perf_counter() - start)

        return result["messages"][-1].content


