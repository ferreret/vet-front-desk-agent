# The front desk behind a chat-completions address, for a voice platform to ask.
# See docs/voice.md. Everything it needs to know is synthetic and built into the image;
# the keys are not, and come from the environment.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1

COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
RUN uv sync --frozen --no-dev --extra endpoint

# The synthetic clinic, always the same from its seed. No real data exists to copy in.
RUN uv run --no-sync vetdesk generate --seed 42 --out /app/data \
    && useradd --system --no-create-home vetdesk \
    && mkdir /app/state && chown vetdesk /app/state
# Where the appointment book is kept when VETDESK_AGENDA points into it. Mount a volume
# here: without one it is gone with the container, like everything else in it.
VOLUME /app/state
# The clinic's clock, not the server's: opening hours and the greeting depend on it.
ENV VETDESK_DATA=/app/data TZ=Europe/Madrid
USER vetdesk

EXPOSE 8013
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD \
    ["/app/.venv/bin/python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8013/health', timeout=3)"]

# Needs GEMINI_API_KEY (or another provider's key, with VETDESK_LLM_MODEL) and
# VETDESK_ENDPOINT_KEY in the environment.
CMD ["/app/.venv/bin/python", "-m", "vetdesk.voice.endpoint", "--host", "0.0.0.0", "--port", "8013"]
