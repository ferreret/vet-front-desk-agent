"""Language-model access, behind a provider-neutral interface."""

from __future__ import annotations

import os

from .base import Conversation, LLMClient, LLMError, Reply, ToolCall, ToolResult, ToolSpec, Usage

PROVIDERS = ("anthropic",)


def create_client(provider: str | None = None, model: str | None = None) -> LLMClient:
    """Build the client for a provider. Adding a provider means adding one adapter here.

    Defaults come from VETDESK_LLM_PROVIDER, VETDESK_LLM_MODEL and VETDESK_LLM_EFFORT.
    """
    provider = provider or os.environ.get("VETDESK_LLM_PROVIDER", "anthropic")
    model = model or os.environ.get("VETDESK_LLM_MODEL")
    if provider == "anthropic":
        from .anthropic_client import DEFAULT_EFFORT, DEFAULT_MODEL, AnthropicClient

        effort = os.environ.get("VETDESK_LLM_EFFORT", DEFAULT_EFFORT)
        return AnthropicClient(model or DEFAULT_MODEL, effort=effort)
    raise LLMError(f"unknown provider {provider!r}; available: {', '.join(PROVIDERS)}")


__all__ = [
    "PROVIDERS", "Conversation", "LLMClient", "LLMError", "Reply", "ToolCall", "ToolResult",
    "ToolSpec", "Usage", "create_client",
]
