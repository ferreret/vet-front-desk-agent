"""Adapter for Gemini models, through Google's Gen AI SDK.

The API key comes from the environment (GEMINI_API_KEY or GOOGLE_API_KEY); nothing is
stored here.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from itertools import chain

import httpx
from google import genai
from google.genai import errors, types

from .base import LLMError, OnText, Reply, ToolCall, ToolResult, ToolSpec, Usage

# The model the evaluation was run on, by its own name: the -latest aliases move, and a
# model nobody measured should not start answering the phone by itself.
DEFAULT_MODEL = "gemini-3.5-flash-lite"

_REFUSALS = {"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "RECITATION"}
_RETRYABLE = {408, 429, 500, 502, 503, 504}


def _thinking_ladder(model: str) -> list[types.ThinkingConfig | None]:
    """Ways of asking for as little deliberation as possible, least first.

    These are short spoken turns. Which setting a model accepts cannot be told from its
    name (gemini-3.8-flash refuses the MINIMAL level and takes a budget of 0;
    gemini-3.5-flash-lite is the other way round) and the -latest aliases move, so a
    conversation walks down this list when the API refuses one.
    """
    if model.startswith("gemini-2.0"):
        return [None]  # no thinking to configure
    no_budget = types.ThinkingConfig(thinking_budget=0)
    if model.startswith("gemini-2.5"):
        return [no_budget, None]
    return [
        types.ThinkingConfig(thinking_level=types.ThinkingLevel.MINIMAL),
        no_budget,
        types.ThinkingConfig(thinking_level=types.ThinkingLevel.LOW),
        None,
    ]


class GeminiClient:
    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        *,
        max_tokens: int = 4096,
        client: genai.Client | None = None,
    ) -> None:
        self.model = model
        self.max_tokens = max_tokens
        self._ladder = _thinking_ladder(model)
        try:
            self._client = client or genai.Client()
        except ValueError as error:
            raise LLMError("no Gemini API key: set GEMINI_API_KEY in .env") from error

    def start(self, system: str, context: str, tools: list[ToolSpec]) -> _Conversation:
        return _Conversation(self, system, context, tools)


class _Conversation:
    def __init__(
        self, owner: GeminiClient, system: str, context: str, tools: list[ToolSpec]
    ) -> None:
        self._owner = owner
        declarations = [
            types.FunctionDeclaration(
                name=t.name, description=t.description, parameters_json_schema=t.parameters
            )
            for t in tools
        ]
        self.config = types.GenerateContentConfig(
            # The part that never changes goes first, so the provider can cache it.
            system_instruction=f"{system}\n\n{context}",
            tools=[types.Tool(function_declarations=declarations)],
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            max_output_tokens=owner.max_tokens,
        )
        # Earlier turns are never edited; the last ones can be taken back whole.
        self._contents: list[types.Content] = []
        self._calls: dict[str, types.FunctionCall] = {}

    def mark(self) -> int:
        return len(self._contents)

    def rewind(self, mark: int) -> None:
        del self._contents[mark:]

    def send_user(self, text: str, on_text: OnText | None = None) -> Reply:
        content = types.Content(role="user", parts=[types.Part.from_text(text=text)])
        return self._complete(content, on_text)

    def send_tool_results(
        self, results: list[ToolResult], on_text: OnText | None = None
    ) -> Reply:
        parts = []
        for result in results:
            call = self._calls[result.call_id]
            answer = types.FunctionResponse(
                id=call.id, name=call.name, response=json.loads(result.content)
            )
            parts.append(types.Part(function_response=answer))
        # Tool results go back as a user turn: the API has no role of its own for them.
        return self._complete(types.Content(role="user", parts=parts), on_text)

    def _open(self) -> Iterator[types.GenerateContentResponse]:
        """Start the reply as a stream, stepping down the thinking ladder while the model
        refuses the setting. A refusal comes before any text, so nothing is said twice."""
        owner = self._owner
        ladder = owner._ladder
        refused: errors.APIError | None = None
        for rung, thinking in enumerate(ladder):
            try:
                stream = iter(owner._client.models.generate_content_stream(
                    model=owner.model,
                    contents=self._contents,
                    config=self.config.model_copy(update={"thinking_config": thinking}),
                ))
                first = next(stream, None)
            except errors.APIError as error:
                if error.code != 400:
                    raise
                refused = refused or error
                continue
            owner._ladder = ladder[rung:]  # what was refused is not asked for again
            return chain([] if first is None else [first], stream)
        # Nothing on the ladder helped, so the 400 was about something else.
        raise refused

    def _complete(self, content: types.Content, on_text: OnText | None) -> Reply:
        self._contents.append(content)
        parts: list[types.Part] = []
        answered, finish, usage = False, "", None
        try:
            for chunk in self._open():
                usage = chunk.usage_metadata or usage
                candidate = chunk.candidates[0] if chunk.candidates else None
                if candidate is None:
                    continue
                answered = True
                if candidate.finish_reason:
                    finish = candidate.finish_reason.name
                for part in (candidate.content.parts or []) if candidate.content else []:
                    parts.append(part)
                    if part.text and not part.thought and on_text:
                        on_text(part.text)
        except errors.APIError as error:
            self._contents.pop()  # leave the history as it was, so the turn can be retried
            raise LLMError(f"{error.code}: {error.message}", error.code in _RETRYABLE) from error
        except httpx.HTTPError as error:
            self._contents.pop()
            raise LLMError("could not reach the provider", True) from error

        if parts:
            # Keep the parts exactly as they came: they may carry signatures the API
            # expects back.
            self._contents.append(types.Content(role="model", parts=parts))

        calls = []
        for part in parts:
            if part.function_call:
                call = part.function_call
                call_id = call.id or f"{call.name}-{len(self._calls) + 1}"
                self._calls[call_id] = call
                calls.append(ToolCall(call_id, call.name, dict(call.args or {})))
        # Pieces of one streamed sentence carry their own spaces.
        text = "".join(p.text for p in parts if p.text and not p.thought).strip()

        if calls:
            stop = "tool_calls"
        elif not answered or finish in _REFUSALS:
            stop = "refusal"
        elif finish == "MAX_TOKENS":
            stop = "max_tokens"
        else:
            stop = "end"

        cached = (usage.cached_content_token_count or 0) if usage else 0
        prompt = (usage.prompt_token_count or 0) if usage else 0
        produced = ((usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0)
                    if usage else 0)
        return Reply(text, tuple(calls), stop, Usage(prompt - cached, produced, cached, 0))
