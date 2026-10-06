"""Adapter for Claude models, through the official Anthropic SDK.

Credentials come from the environment (ANTHROPIC_API_KEY, or a profile created with
`ant auth login`); nothing is stored here. An API key that is not tied to a workspace also
needs ANTHROPIC_WORKSPACE_ID, which is sent as the `anthropic-workspace-id` header.
"""

from __future__ import annotations

import os

import anthropic

from .base import LLMError, OnText, Reply, ToolCall, ToolResult, ToolSpec, Usage

DEFAULT_MODEL = "claude-sonnet-5-5"
DEFAULT_EFFORT = "low"  # short spoken turns: favour latency over deliberation

# Server-side refusal fallback: if the model's safety classifiers decline a request, the
# API re-runs it on Anthropic's recommended substitute within the same call.
FALLBACK_BETA = "server-side-fallback-2026-07-01"
_SUPPORTS_FALLBACK = ("claude-opus-5", "claude-sonnet-5-5", "claude-fable-5-1")
_NO_EFFORT = ("claude-haiku-4-5",)  # rejects output_config.effort
# The only model whose thinking can be switched off, with its own setting for it.
_THINKING_OFF = {"claude-sonnet-5-5": {"type": "between_tools"}}

_STOP = {"end_turn": "end", "tool_use": "tool_calls", "max_tokens": "max_tokens",
         "refusal": "refusal"}


class AnthropicClient:
    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        *,
        effort: str | None = DEFAULT_EFFORT,
        thinking: bool = False,
        max_tokens: int = 4096,
        fallbacks: bool = True,
        client: anthropic.Anthropic | None = None,
    ) -> None:
        self.model = model
        self.effort = None if model.startswith(_NO_EFFORT) else effort
        # No thinking by default: measured on a phone-style call, the model then speaks
        # before it reaches for a tool, so the caller hears something in under two seconds
        # instead of waiting for the whole turn. Honoured where the model allows it.
        self.thinking = None if thinking else _THINKING_OFF.get(model)
        self.max_tokens = max_tokens
        self.fallbacks = fallbacks and model.startswith(_SUPPORTS_FALLBACK)
        self._client = client or anthropic.Anthropic(default_headers=_workspace_header())

    def start(self, system: str, context: str, tools: list[ToolSpec]) -> _Conversation:
        return _Conversation(self, system, context, tools)


def _workspace_header() -> dict[str, str] | None:
    workspace = os.environ.get("ANTHROPIC_WORKSPACE_ID", "").strip()
    return {"anthropic-workspace-id": workspace} if workspace else None


class _Conversation:
    def __init__(
        self, owner: AnthropicClient, system: str, context: str, tools: list[ToolSpec]
    ) -> None:
        self._owner = owner
        # The system text is identical on every call and is cached; what is specific to
        # this call comes after the cache breakpoint so it does not invalidate it.
        self._system = [
            {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": context},
        ]
        # Tool inputs here are a few short fields, so they are left to arrive whole and
        # schema-checked by the API rather than streamed eagerly.
        self._tools = [
            {"name": t.name, "description": t.description, "input_schema": t.parameters,
             "strict": True}
            for t in tools
        ]
        # Earlier turns are never edited; the last ones can be taken back whole.
        self._messages: list[dict] = []

    def mark(self) -> int:
        return len(self._messages)

    def rewind(self, mark: int) -> None:
        del self._messages[mark:]

    def send_user(self, text: str, on_text: OnText | None = None) -> Reply:
        return self._complete({"role": "user", "content": text}, on_text)

    def send_tool_results(
        self, results: list[ToolResult], on_text: OnText | None = None
    ) -> Reply:
        # All results of one turn go back together, in a single user message.
        content = [
            {"type": "tool_result", "tool_use_id": r.call_id, "content": r.content,
             "is_error": r.is_error}
            for r in results
        ]
        return self._complete({"role": "user", "content": content}, on_text)

    def request(self) -> dict:
        owner = self._owner
        params: dict = {
            "model": owner.model,
            "max_tokens": owner.max_tokens,
            "system": self._system,
            "tools": self._tools,
            "messages": self._messages,
            "cache_control": {"type": "ephemeral"},  # also cache the conversation so far
        }
        if owner.effort:
            params["output_config"] = {"effort": owner.effort}
        if owner.thinking:
            params["thinking"] = owner.thinking
        return params

    def _complete(self, message: dict, on_text: OnText | None) -> Reply:
        self._messages.append(message)
        try:
            response = self._stream(self.request(), on_text)
        except anthropic.RateLimitError as error:
            raise self._failed("the provider is rate limiting requests", True) from error
        except anthropic.APIStatusError as error:
            retryable = error.status_code >= 500
            raise self._failed(f"{error.status_code}: {error.message}", retryable) from error
        except anthropic.APIConnectionError as error:
            raise self._failed("could not reach the provider", True) from error

        stop = _STOP.get(response.stop_reason, "end")
        if stop != "refusal":
            # Keep the reply exactly as it came, reasoning blocks included.
            self._messages.append({"role": "assistant", "content": response.content})
        text = " ".join(b.text.strip() for b in response.content if b.type == "text").strip()
        calls = tuple(
            ToolCall(b.id, b.name, dict(b.input)) for b in response.content if b.type == "tool_use"
        )
        usage = response.usage
        return Reply(
            text=text,
            tool_calls=calls,
            stop=stop,
            usage=Usage(
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_read_tokens=getattr(usage, "cache_read_input_tokens", None) or 0,
                cache_write_tokens=getattr(usage, "cache_creation_input_tokens", None) or 0,
            ),
        )

    def _stream(self, params: dict, on_text: OnText | None):
        """Run the request as a stream, handing each piece of text over as it arrives."""
        client = self._owner._client
        if self._owner.fallbacks:
            manager = client.beta.messages.stream(
                **params, betas=[FALLBACK_BETA], fallbacks="default"
            )
        else:
            manager = client.messages.stream(**params)
        with manager as stream:
            for event in stream:
                if event.type == "text" and on_text:
                    on_text(event.text)
            return stream.get_final_message()

    def _failed(self, message: str, retryable: bool) -> LLMError:
        self._messages.pop()  # leave the history as it was, so the turn can be retried
        return LLMError(message, retryable)
