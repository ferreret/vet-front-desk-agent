"""Language-model access, behind a provider-neutral interface."""

from __future__ import annotations

import os

from .base import (
    Conversation,
    LLMClient,
    LLMError,
    OnText,
    Reply,
    ToolCall,
    ToolResult,
    ToolSpec,
    Usage,
)

PROVIDERS = ("anthropic", "gemini", "openai", "requesty")
# Chosen by measuring (2026-10-05): the same scenarios played with models from four
# providers. Gemini's small model was the only one answering inside the 1.5 s a phone call
# allows, and what it got wrong was moved into code. See docs/evaluation.md.
DEFAULT_PROVIDER = "gemini"


def provider_of(model: str | None) -> str | None:
    """The provider a model id belongs to, when the id makes it obvious."""
    if model and model.startswith("claude"):
        return "anthropic"
    if model and model.startswith("gemini"):
        return "gemini"
    if model and "/" in model:
        return "requesty"  # the router's ids name who serves the model: zai/glm-5.3-flash
    if model and model.startswith(("gpt-", "o1", "o3", "o4")):
        return "openai"
    return None


def create_client(
    provider: str | None = None,
    model: str | None = None,
    *,
    effort: str | None = None,
    thinking: bool | None = None,
) -> LLMClient:
    """Build the client for a provider. Adding a provider means adding one adapter here.

    Defaults come from VETDESK_LLM_PROVIDER and VETDESK_LLM_MODEL and, for Claude models,
    VETDESK_LLM_EFFORT and VETDESK_LLM_THINKING (off by default, where it can be). A
    model id that names its provider (claude-..., gemini-..., gpt-..., or the router's
    served-by/model form) needs no provider. `effort`
    and `thinking` override the environment, for uses other than answering the phone (a
    judge reading a transcript is in no hurry).
    """
    model = model or os.environ.get("VETDESK_LLM_MODEL")
    provider = (provider or provider_of(model)
                or os.environ.get("VETDESK_LLM_PROVIDER", DEFAULT_PROVIDER))
    if provider == "anthropic":
        from .anthropic_client import DEFAULT_EFFORT, DEFAULT_MODEL, AnthropicClient

        effort = effort or os.environ.get("VETDESK_LLM_EFFORT", DEFAULT_EFFORT)
        if thinking is None:
            thinking = os.environ.get("VETDESK_LLM_THINKING", "off").lower() == "on"
        return AnthropicClient(model or DEFAULT_MODEL, effort=effort, thinking=thinking)
    if provider == "gemini":
        from .gemini_client import DEFAULT_MODEL, GeminiClient

        return GeminiClient(model or DEFAULT_MODEL)
    if provider in ("openai", "requesty"):
        from .openai_client import DEFAULT_MODEL, OpenAICompatClient

        if provider == "requesty" and not model:
            raise LLMError("the router needs a model id, e.g. zai/glm-5.3-flash")
        return OpenAICompatClient(model or DEFAULT_MODEL, provider=provider)
    raise LLMError(f"unknown provider {provider!r}; available: {', '.join(PROVIDERS)}")


__all__ = [
    "DEFAULT_PROVIDER", "PROVIDERS", "Conversation", "LLMClient", "LLMError", "OnText", "Reply",
    "ToolCall", "ToolResult", "ToolSpec", "Usage", "create_client", "provider_of",
]
