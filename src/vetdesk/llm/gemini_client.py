"""Adapter for Gemini models, through Google's Gen AI SDK.

The API key comes from the environment (GEMINI_API_KEY or GOOGLE_API_KEY); nothing is
stored here.
"""

from __future__ import annotations

import json

import httpx
from google import genai
from google.genai import errors, types

from .base import LLMError, OnText, Reply, ToolCall, ToolResult, ToolSpec, Usage

DEFAULT_MODEL = "gemini-flash-latest"

_REFUSALS = {"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "RECITATION"}
_RETRYABLE = {408, 429, 500, 502, 503, 504}


def _thinking(model: str) -> types.ThinkingConfig | None:
    """As little deliberation as the model allows: these are short spoken turns."""
    if model.startswith("gemini-2.0"):
        return None  # no thinking to configure
    if model.startswith("gemini-2.5"):
        return types.ThinkingConfig(thinking_budget=0)
    return types.ThinkingConfig(thinking_level=types.ThinkingLevel.MINIMAL)


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
            thinking_config=_thinking(owner.model),
        )
        self._contents: list[types.Content] = []  # append-only
        self._calls: dict[str, types.FunctionCall] = {}

    def send_user(self, text: str, on_text: OnText | None = None) -> Reply:
        content = types.Content(role="user", parts=[types.Part.from_text(text=text)])
        return self._spoken(self._complete(content), on_text)

    @staticmethod
    def _spoken(reply: Reply, on_text: OnText | None) -> Reply:
        # Not streamed yet: the text is handed over whole, once the reply is complete.
        if reply.text and on_text:
            on_text(reply.text)
        return reply

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
        return self._spoken(self._complete(types.Content(role="tool", parts=parts)), on_text)

    def _complete(self, content: types.Content) -> Reply:
        self._contents.append(content)
        try:
            response = self._owner._client.models.generate_content(
                model=self._owner.model, contents=self._contents, config=self.config
            )
        except errors.APIError as error:
            self._contents.pop()  # leave the history as it was, so the turn can be retried
            raise LLMError(f"{error.code}: {error.message}", error.code in _RETRYABLE) from error
        except httpx.HTTPError as error:
            self._contents.pop()
            raise LLMError("could not reach the provider", True) from error

        candidate = response.candidates[0] if response.candidates else None
        answer = candidate.content if candidate else None
        parts = (answer.parts or []) if answer else []
        if parts:
            # Keep the reply exactly as it came: it may carry signatures the API expects back.
            self._contents.append(answer)

        calls = []
        for part in parts:
            if part.function_call:
                call = part.function_call
                call_id = call.id or f"{call.name}-{len(self._calls) + 1}"
                self._calls[call_id] = call
                calls.append(ToolCall(call_id, call.name, dict(call.args or {})))
        text = " ".join(p.text.strip() for p in parts if p.text and not p.thought).strip()

        finish = candidate.finish_reason.name if candidate and candidate.finish_reason else ""
        if calls:
            stop = "tool_calls"
        elif candidate is None or finish in _REFUSALS:
            stop = "refusal"
        elif finish == "MAX_TOKENS":
            stop = "max_tokens"
        else:
            stop = "end"

        usage = response.usage_metadata
        cached = (usage.cached_content_token_count or 0) if usage else 0
        prompt = (usage.prompt_token_count or 0) if usage else 0
        produced = ((usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0)
                    if usage else 0)
        return Reply(text, tuple(calls), stop, Usage(prompt - cached, produced, cached, 0))
