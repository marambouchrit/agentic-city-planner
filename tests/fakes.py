"""A scripted LLM for offline tests. It plays the LLM's decisions, so no quota is used."""

from city_planner.llm import LLMResponse, ToolCall


def call(tool_name, /, **args):
    return ToolCall(tool_name, args)


class FakeLLM:
    """Each script item is one LLM answer: a list of tool calls, a text, or an exception."""

    def __init__(self, script):
        self.script = list(script)
        self.requests = []

    def generate(self, system, messages, tools, only_tools=None):
        self.requests.append(
            {"system": system, "messages": list(messages), "tools": [t["name"] for t in tools], "only_tools": only_tools}
        )
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, str):
            return LLMResponse(text=item, raw={"text": item})
        return LLMResponse(calls=item, raw={"calls": [c.name for c in item]})
