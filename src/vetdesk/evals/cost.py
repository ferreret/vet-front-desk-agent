"""What a model's tokens cost."""

from __future__ import annotations

from ..llm import Usage

# US dollars per million tokens (input, output), first-party API prices as of 2026-09.
# Cached input is billed at a tenth of the input price; writing the cache at 1.25 times.
# The Gemini prices are the list prices in Requesty's catalogue on 2026-10-05; Gemini caches
# on its own and charges nothing for writing. The -latest aliases move: on that day they
# pointed at the models priced here.
PRICES = {
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-haiku-5-5": (0.10, 0.50),  # for prompts up to 100K tokens, as these all are
    "gemini-3.8-flash": (0.75, 3.75),
    "gemini-flash-latest": (0.75, 3.75),
    "gemini-3.5-flash-lite": (0.30, 2.50),
    "gemini-flash-lite-latest": (0.30, 2.50),
    # OpenAI, and one model through the Requesty router: same catalogue, same day.
    "gpt-5.4-mini": (0.75, 4.50),
    "gpt-5.6-luna": (0.20, 1.20),
    "gpt-6-luna": (0.10, 0.50),
    "gpt-6-sol": (2.0, 10.0),
    "lyceum/glm-5.3-flash": (0.20, 0.50),
}


def cost(model: str, usage: Usage) -> float | None:
    """Dollars for `usage` on `model`, or None when the model's price is not known."""
    if model not in PRICES:
        return None
    price_in, price_out = PRICES[model]
    tokens_in = (usage.input_tokens + 0.1 * usage.cache_read_tokens
                 + 1.25 * usage.cache_write_tokens)
    return (tokens_in * price_in + usage.output_tokens * price_out) / 1_000_000
