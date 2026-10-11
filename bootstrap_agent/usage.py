"""Token usage of one turn, in total and per graph step (node), from the usage_metadata of each LLM reply."""

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages.ai import add_usage


class TokenUsage(BaseCallbackHandler):
    """Callback that adds up the token usage of every LLM call in a run, keyed by LangGraph node.

    Calls inside the ReAct agent count under "agent". Calls whose provider reports no usage are not counted.
    """

    run_inline = True  # update the totals in order, on the event loop

    def __init__(self):
        self._steps = {}  # LLM run id -> graph node that made the call
        self.by_step = {}

    def on_chat_model_start(self, serialized, messages, *, run_id, metadata=None, **kwargs):
        self._steps[run_id] = (metadata or {}).get("langgraph_node", "unknown")

    def on_llm_end(self, response, *, run_id, **kwargs):
        step = self._steps.pop(run_id, "unknown")
        for generation in (g for gs in response.generations for g in gs):
            usage = getattr(getattr(generation, "message", None), "usage_metadata", None)
            if usage:
                current = self.by_step.get(step, {"llm_calls": 0})
                self.by_step[step] = {**add_usage(current, usage), "llm_calls": current["llm_calls"] + 1}

    def summary(self) -> dict | None:
        """{"input_tokens", "output_tokens", "total_tokens", "llm_calls", "by_step": {node: {...}}},
        or None if no call reported usage."""
        if not self.by_step:
            return None
        total = {"llm_calls": 0}
        for step in self.by_step.values():
            total = {**add_usage(total, step), "llm_calls": total["llm_calls"] + step["llm_calls"]}
        return {**_ordered(total), "by_step": {name: _ordered(step) for name, step in self.by_step.items()}}

    def log_line(self) -> str:
        usage = self.summary()
        if usage is None:
            return "token usage not reported by the model provider"
        steps = ", ".join(f"{name} {step['total_tokens']}" for name, step in self.by_step.items())
        return (f"tokens {usage['total_tokens']} (input {usage['input_tokens']}, output {usage['output_tokens']}) "
                f"in {usage['llm_calls']} LLM calls; by step: {steps}")


def _ordered(usage: dict) -> dict:
    """Main counts first, then provider details such as input_token_details (cache_read) and reasoning."""
    main = ("input_tokens", "output_tokens", "total_tokens", "llm_calls")
    return {**{key: usage[key] for key in main}, **{key: value for key, value in usage.items() if key not in main}}
