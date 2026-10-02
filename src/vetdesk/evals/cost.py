"""What a model's tokens cost."""

from __future__ import annotations

from ..llm import Usage

# US dollars per million tokens (input, output), first-party API prices as of 2026-09.
# Cached input is billed at a tenth of the input price; writing the cache at 1.25 times.
PRICES = {
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-haiku-4-5": (1.0, 5.0),
}


def cost(model: str, usage: Usage) -> float | None:
    """Dollars for `usage` on `model`, or None when the model's price is not known."""
    if model not in PRICES:
        return None
    price_in, price_out = PRICES[model]
    tokens_in = (usage.input_tokens + 0.1 * usage.cache_read_tokens
                 + 1.25 * usage.cache_write_tokens)
    return (tokens_in * price_in + usage.output_tokens * price_out) / 1_000_000
