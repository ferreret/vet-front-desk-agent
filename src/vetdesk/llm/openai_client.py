"""Adapter for anything that speaks OpenAI's chat-completions format.

Two providers use it: OpenAI itself, and Requesty, a router that reaches many other models
(DeepSeek, GLM, Qwen, MiniMax...) through the same format at another address. The API key
comes from the environment (OPENAI_API_KEY or REQUESTY_API_KEY); nothing is stored here.
"""

from __future__ import annotations

import json
import os

import openai

from .base import LLMError, OnText, Reply, ToolCall, ToolResult, ToolSpec, Usage

DEFAULT_MODEL = "gpt-6-luna"
REQUESTY_URL = "https://router.requesty.ai/v1"

# Ways of asking for as little deliberation as possible, least first: these are short
# spoken turns. Which value a model accepts varies and cannot be told from its name, so a
# conversation walks down the list when the API refuses one. None leaves the field out.
_EFFORTS: tuple[str | None, ...] = ("none", "minimal", "low", None)
_STOP = {"stop": "end", "tool_calls": "tool_calls", "length": "max_tokens",
         "content_filter": "refusal"}


class OpenAICompatClient:
    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        *,
        provider: str = "openai",
        max_tokens: int = 4096,
        client: openai.OpenAI | None = None,
    ) -> None:
        self.model = model
        self.provider = provider
        self.max_tokens = max_tokens
        self._efforts = _EFFORTS
        if client is None:
            key_name = "REQUESTY_API_KEY" if provider == "requesty" else "OPENAI_API_KEY"
            key = os.environ.get(key_name)
            if not key:
                raise LLMError(f"no API key: set {key_name} in .env")
            client = openai.OpenAI(
                api_key=key, base_url=REQUESTY_URL if provider == "requesty" else None
            )
        self._client = client

    def start(self, system: str, context: str, tools: list[ToolSpec]) -> _Conversation:
        return _Conversation(self, system, context, tools)


class _Conversation:
    def __init__(
        self, owner: OpenAICompatClient, system: str, context: str, tools: list[ToolSpec]
    ) -> None:
        self._owner = owner
        # The part that never changes goes first, so the provider can cache it.
        self._messages: list[dict] = [{"role": "system", "content": f"{system}\n\n{context}"}]
        self._tools = []
        for tool in tools:
            function = {"name": tool.name, "description": tool.description,
                        "parameters": tool.parameters}
            if owner.provider == "openai":
                function["strict"] = True  # not every model behind the router takes it
            self._tools.append({"type": "function", "function": function})

    def mark(self) -> int:
        return len(self._messages)

    def rewind(self, mark: int) -> None:
        del self._messages[mark:]

    def send_user(self, text: str, on_text: OnText | None = None) -> Reply:
        return self._complete([{"role": "user", "content": text}], on_text)

    def send_tool_results(
        self, results: list[ToolResult], on_text: OnText | None = None
    ) -> Reply:
        answers = [{"role": "tool", "tool_call_id": r.call_id, "content": r.content}
                   for r in results]
        return self._complete(answers, on_text)

    def request(self, effort: str | None) -> dict:
        owner = self._owner
        params: dict = {
            "model": owner.model,
            "messages": self._messages,
            "tools": self._tools,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        # OpenAI's own models take the newer name for the limit; the router the classic one.
        limit = "max_completion_tokens" if owner.provider == "openai" else "max_tokens"
        params[limit] = owner.max_tokens
        if effort:
            params["reasoning_effort"] = effort
        return params

    def _open(self):
        """Start the reply as a stream, stepping down the list of efforts while the model
        refuses the value. A refusal comes before any text, so nothing is said twice."""
        owner = self._owner
        efforts = owner._efforts
        refused: openai.BadRequestError | None = None
        for step, effort in enumerate(efforts):
            try:
                stream = owner._client.chat.completions.create(**self.request(effort))
            except openai.BadRequestError as error:
                refused = error
                continue
            owner._efforts = efforts[step:]  # what was refused is not asked for again
            return stream
        # No effort helped, so the 400 is about something else. The last refusal, with the
        # field left out, is the one that says what: the first only complains of the value.
        raise refused

    def _complete(self, messages: list[dict], on_text: OnText | None) -> Reply:
        before = len(self._messages)
        self._messages.extend(messages)
        pieces: list[str] = []
        calls: dict[int, dict] = {}
        finish, usage, refused = "", None, False
        try:
            for chunk in self._open():
                usage = getattr(chunk, "usage", None) or usage
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                finish = choice.finish_reason or finish
                delta = choice.delta
                if delta is None:
                    continue
                refused = refused or bool(getattr(delta, "refusal", None))
                if delta.content:
                    pieces.append(delta.content)
                    if on_text:
                        on_text(delta.content)
                for part in delta.tool_calls or []:
                    # A call arrives in pieces: its id and name once, its arguments bit by bit.
                    call = calls.setdefault(part.index, {"id": "", "name": "", "arguments": ""})
                    call["id"] = part.id or call["id"]
                    if part.function:
                        call["name"] = part.function.name or call["name"]
                        call["arguments"] += part.function.arguments or ""
        except openai.RateLimitError as error:
            raise self._failed(before, "the provider is rate limiting requests", True) from error
        except openai.APIStatusError as error:
            retryable = error.status_code >= 500
            raise self._failed(before, f"{error.status_code}: {error.message}",
                               retryable) from error
        except openai.APIConnectionError as error:
            raise self._failed(before, "could not reach the provider", True) from error

        text = "".join(pieces).strip()
        ordered = [calls[index] for index in sorted(calls)]
        tool_calls = []
        for position, call in enumerate(ordered, start=1):
            call["id"] = call["id"] or f"{call['name']}-{before}-{position}"
            try:
                arguments = json.loads(call["arguments"] or "{}")
            except json.JSONDecodeError:
                arguments = {}  # the tool answers "bad arguments", and the model tries again
            tool_calls.append(ToolCall(call["id"], call["name"], arguments))

        stop = "tool_calls" if tool_calls else "refusal" if refused else _STOP.get(finish, "end")
        if stop != "refusal":
            answer: dict = {"role": "assistant", "content": text or None}
            if ordered:
                answer["tool_calls"] = [
                    {"id": c["id"], "type": "function",
                     "function": {"name": c["name"], "arguments": c["arguments"] or "{}"}}
                    for c in ordered
                ]
            self._messages.append(answer)

        prompt = (getattr(usage, "prompt_tokens", 0) or 0) if usage else 0
        produced = (getattr(usage, "completion_tokens", 0) or 0) if usage else 0
        details = getattr(usage, "prompt_tokens_details", None) if usage else None
        cached = (getattr(details, "cached_tokens", 0) or 0) if details else 0
        return Reply(text, tuple(tool_calls), stop, Usage(prompt - cached, produced, cached, 0))

    def _failed(self, before: int, message: str, retryable: bool) -> LLMError:
        del self._messages[before:]  # leave the history as it was, so the turn can be retried
        return LLMError(message, retryable)
