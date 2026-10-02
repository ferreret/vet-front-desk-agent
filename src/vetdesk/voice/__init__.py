"""The voice layer: the same front-desk agent, heard and spoken.

Speech recognition turns what the caller says into text, the agent answers exactly as it
does in text, and speech synthesis says the answer. Nothing about who is calling or what
may be said changes here: that stays in the agent and its tools.

Needs the `voice` extra: `uv sync --extra voice`.
"""
