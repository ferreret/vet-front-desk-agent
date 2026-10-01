"""Language-model access, behind a provider-neutral interface."""

from __future__ import annotations

import os

from .base import Conversation, LLMClient, LLMError, Reply, ToolCall, ToolResult, ToolSpec, Usage

PROVIDERS = ("anthropic", "gemini")


def provider_of(model: str | None) -> str | None:
    """The provider a model id belongs to, when the id makes it obvious."""
    if model and model.startswith("claude"):
        return "anthropic"
    if model and model.startswith("gemini"):
        return "gemini"
    return None


def create_client(provider: str | None = None, model: str | None = None) -> LLMClient:
    """Build the client for a provider. Adding a provider means adding one adapter here.

    Defaults come from VETDESK_LLM_PROVIDER, VETDESK_LLM_MODEL and VETDESK_LLM_EFFORT. A
    model id that names its provider (claude-..., gemini-...) needs no provider.
    """
    model = model or os.environ.get("VETDESK_LLM_MODEL")
    provider = (provider or provider_of(model)
                or os.environ.get("VETDESK_LLM_PROVIDER", "anthropic"))
    if provider == "anthropic":
        from .anthropic_client import DEFAULT_EFFORT, DEFAULT_MODEL, AnthropicClient

        effort = os.environ.get("VETDESK_LLM_EFFORT", DEFAULT_EFFORT)
        return AnthropicClient(model or DEFAULT_MODEL, effort=effort)
    if provider == "gemini":
        from .gemini_client import DEFAULT_MODEL, GeminiClient

        return GeminiClient(model or DEFAULT_MODEL)
    raise LLMError(f"unknown provider {provider!r}; available: {', '.join(PROVIDERS)}")


__all__ = [
    "PROVIDERS", "Conversation", "LLMClient", "LLMError", "Reply", "ToolCall", "ToolResult",
    "ToolSpec", "Usage", "create_client", "provider_of",
]
