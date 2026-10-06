"""A model that follows a script. For tests and offline demos: no network, no cost."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from .base import LLMError, OnText, Reply, ToolResult, ToolSpec

# A step is a ready-made reply, or a function that builds one from what the model was sent.
Step = Reply | Callable[["Transcript"], Reply]


@dataclass
class Transcript:
    """Everything the scripted model has been sent so far."""

    system: str = ""
    context: str = ""
    tools: list[ToolSpec] = field(default_factory=list)
    user_messages: list[str] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)


class ScriptedClient:
    def __init__(self, steps: list[Step]) -> None:
        self._steps = list(steps)
        self.transcript = Transcript()

    def start(self, system: str, context: str, tools: list[ToolSpec]) -> ScriptedClient:
        self.transcript.system, self.transcript.context = system, context
        self.transcript.tools = list(tools)
        return self

    def _next(self) -> Reply:
        if not self._steps:
            raise LLMError("the script has run out of steps")
        step = self._steps.pop(0)
        return step(self.transcript) if callable(step) else step

    def _reply(self, on_text: OnText | None) -> Reply:
        reply = self._next()
        if reply.text and on_text:
            on_text(reply.text)
        return reply

    def mark(self) -> int:
        return len(self.transcript.user_messages)

    def rewind(self, mark: int) -> None:
        del self.transcript.user_messages[mark:]

    def send_user(self, text: str, on_text: OnText | None = None) -> Reply:
        self.transcript.user_messages.append(text)
        return self._reply(on_text)

    def send_tool_results(
        self, results: list[ToolResult], on_text: OnText | None = None
    ) -> Reply:
        self.transcript.tool_results.extend(results)
        return self._reply(on_text)
