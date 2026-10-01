"""The small interface the agent talks to a language model through.

The agent never imports a provider SDK. It starts a conversation, sends what the caller
said or what its tools returned, and gets back text and tool calls. Each provider is one
adapter implementing these two protocols; the conversation object keeps the provider's own
message history, so provider-specific details (reasoning blocks, cache markers) never leak
into the agent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict  # JSON Schema of the arguments


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass(frozen=True)
class ToolResult:
    call_id: str
    content: str
    is_error: bool = False


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.cache_read_tokens + other.cache_read_tokens,
            self.cache_write_tokens + other.cache_write_tokens,
        )


@dataclass(frozen=True)
class Reply:
    text: str
    tool_calls: tuple[ToolCall, ...] = ()
    # end: the model finished its turn. tool_calls: it wants tools run. max_tokens: it was
    # cut off. refusal: the provider declined to answer.
    stop: Literal["end", "tool_calls", "max_tokens", "refusal"] = "end"
    usage: Usage = field(default_factory=Usage)


class LLMError(Exception):
    """The provider could not answer. `retryable` says whether trying again may help."""

    def __init__(self, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class Conversation(Protocol):
    def send_user(self, text: str) -> Reply: ...

    def send_tool_results(self, results: list[ToolResult]) -> Reply: ...


class LLMClient(Protocol):
    def start(self, system: str, context: str, tools: list[ToolSpec]) -> Conversation:
        """Open a conversation. `system` is the same for every call and can be cached;
        `context` holds what is specific to this call (the time, the calling number)."""
        ...
