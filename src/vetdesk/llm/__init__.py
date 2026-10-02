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

PROVIDERS = ("anthropic", "gemini")


def provider_of(model: str | None) -> str | None:
    """The provider a model id belongs to, when the id makes it obvious."""
    if model and model.startswith("claude"):
        return "anthropic"
    if model and model.startswith("gemini"):
        return "gemini"
    return None


def create_client(
    provider: str | None = None,
    model: str | None = None,
    *,
    effort: str | None = None,
    thinking: bool | None = None,
) -> LLMClient:
    """Build the client for a provider. Adding a provider means adding one adapter here.

    Defaults come from VETDESK_LLM_PROVIDER, VETDESK_LLM_MODEL, VETDESK_LLM_EFFORT and
    VETDESK_LLM_THINKING (off by default, on the models that allow switching it off). A
    model id that names its provider (claude-..., gemini-...) needs no provider. `effort`
    and `thinking` override the environment, for uses other than answering the phone (a
    judge reading a transcript is in no hurry).
    """
    model = model or os.environ.get("VETDESK_LLM_MODEL")
    provider = (provider or provider_of(model)
                or os.environ.get("VETDESK_LLM_PROVIDER", "anthropic"))
    if provider == "anthropic":
        from .anthropic_client import DEFAULT_EFFORT, DEFAULT_MODEL, AnthropicClient

        effort = effort or os.environ.get("VETDESK_LLM_EFFORT", DEFAULT_EFFORT)
        if thinking is None:
            thinking = os.environ.get("VETDESK_LLM_THINKING", "off").lower() == "on"
        return AnthropicClient(model or DEFAULT_MODEL, effort=effort, thinking=thinking)
    if provider == "gemini":
        from .gemini_client import DEFAULT_MODEL, GeminiClient

        return GeminiClient(model or DEFAULT_MODEL)
    raise LLMError(f"unknown provider {provider!r}; available: {', '.join(PROVIDERS)}")


__all__ = [
    "PROVIDERS", "Conversation", "LLMClient", "LLMError", "OnText", "Reply", "ToolCall",
    "ToolResult", "ToolSpec", "Usage", "create_client", "provider_of",
]
