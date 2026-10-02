"""The front desk on a voice line, through LiveKit Agents.

LiveKit does the hard real-time part: listening, deciding when the caller has finished,
letting them interrupt, playing the answer. ElevenLabs hears (Scribe) and speaks. The
thinking is not theirs: every answer comes from `FrontDeskAgent`, the same one the
evaluation harness measures, reached through LiveKit's `llm_node`.

Run it with the computer's microphone and speakers, no server needed:

    uv run python -m vetdesk.voice console
"""

from __future__ import annotations

import json
import logging
import os
import sys
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path

from livekit.agents import Agent, AgentServer, AgentSession, JobContext, JobProcess, cli, llm
from livekit.plugins import elevenlabs, silero

from ..agent import FrontDeskAgent
from ..agent.agent import Call, Turn
from ..kb import load_kb
from ..legacy import LegacySqliteSource
from ..llm import create_client
from ..scheduling import SqliteAgenda
from .bridge import Line, language_of

log = logging.getLogger("vetdesk.voice")


def _load_env(path: Path = Path(".env")) -> None:
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            key, separator, value = line.strip().partition("=")
            if separator and key and not key.startswith("#"):
                os.environ.setdefault(key.strip(), value.strip().strip("'\""))


_load_env()  # before the settings below are read, in whichever process imports this

DATA = Path(os.environ.get("VETDESK_DATA", "data"))
# Catalan rules out the fastest ElevenLabs model (Flash v2.5 does not speak it). Of the two
# that do and can stream, v4 Turbo is the quicker.
TTS_MODEL = os.environ.get("VETDESK_TTS_MODEL", "eleven_v4_turbo")
TTS_VOICE = os.environ.get("VETDESK_TTS_VOICE")  # an ElevenLabs voice id; theirs by default
STT_MODEL = os.environ.get("VETDESK_STT_MODEL", "scribe_v2_realtime")
# The first is the clinic's language; the others are what callers may speak instead. The
# clinic this is meant for is on a tourist coast: Catalan, and the visitors' languages.
STT_LANGUAGES = os.environ.get("VETDESK_STT_LANGUAGES", "es,ca,en,fr,de,nl,it").split(",")


class _ElsewhereLLM(llm.LLM):
    """LiveKit only answers a caller when the session has a model. Ours lives in
    `FrontDeskAgent` and is reached through `llm_node`; this one is never asked."""

    def chat(self, **kwargs):  # pragma: no cover - never called
        raise NotImplementedError("the front desk answers through llm_node")


class VoiceFrontDesk(Agent):
    def __init__(self, call: Call) -> None:
        # The instructions live with the agent that does the answering, not here.
        super().__init__(instructions="")
        self._line = Line(call)
        self.language = "es"  # updated from what speech recognition hears

    async def llm_node(self, chat_ctx, tools, model_settings) -> AsyncIterator[str]:
        heard = next((item.text_content for item in reversed(chat_ctx.items)
                      if getattr(item, "role", None) == "user" and item.text_content), "")
        async for piece in self._line.answer(heard, self.language, self._turn_done):
            yield piece

    def _turn_done(self, turn: Turn) -> None:
        for event in turn.events:
            log.info("tool %s %s -> %s", event.name,
                     json.dumps(event.arguments, ensure_ascii=False),
                     json.dumps(event.result, ensure_ascii=False)[:300])
        log.info("model: first words %.1f s, complete %.1f s (%s)", turn.first_words or 0,
                 turn.seconds, " + ".join(f"{s:.1f}" for s in turn.latencies))


def prewarm(proc: JobProcess) -> None:
    """Load what takes time before the phone rings, not while the caller waits."""
    _load_env()
    kb = load_kb()
    proc.userdata.update(
        vad=silero.VAD.load(),
        kb=kb,
        clinic=LegacySqliteSource(DATA / "clinic.db").load(),
        llm=create_client(),
    )


server = AgentServer(setup_fnc=prewarm)


@server.rtc_session()
async def entrypoint(ctx: JobContext) -> None:
    ready = ctx.proc.userdata
    kb = ready["kb"]
    front_desk = FrontDeskAgent(ready["llm"], ready["clinic"], kb,
                                SqliteAgenda(kb, datetime.now))
    # No caller ID on a microphone. Set VETDESK_CALLER_NUMBER to try a call "from" a number.
    call = front_desk.start_call(os.environ.get("VETDESK_CALLER_NUMBER") or None)
    agent = VoiceFrontDesk(call)

    session = AgentSession(
        # With a primary language set, the recogniser stops saying which language it
        # heard unless asked to: it then called a Catalan sentence Spanish.
        stt=elevenlabs.STT(model=STT_MODEL, language_code=STT_LANGUAGES[0],
                           secondary_languages=STT_LANGUAGES[1:],
                           include_language_detection=True),
        llm=_ElsewhereLLM(),
        tts=elevenlabs.TTS(model=TTS_MODEL, **({"voice_id": TTS_VOICE} if TTS_VOICE else {})),
        vad=ready["vad"],
    )

    @session.on("user_input_transcribed")
    def _heard(event) -> None:
        if event.is_final:
            agent.language = language_of(event.language)
            log.info("caller (%s): %s", event.language, event.transcript)

    @session.on("conversation_item_added")
    def _said(event) -> None:
        item = event.item
        metrics = getattr(item, "metrics", None) or {}
        if getattr(item, "role", None) == "assistant":
            log.info("agent: %s", item.text_content)
            timings = {k: round(v, 2) for k, v in metrics.items() if isinstance(v, float)
                       and k in ("e2e_latency", "llm_node_ttft", "tts_node_ttfb")}
            if timings:
                log.info("voice: %s", timings)
        elif metrics:
            log.info("listening: %s", {k: round(v, 2) for k, v in metrics.items()
                                       if k in ("transcription_delay", "end_of_turn_delay")})

    await session.start(agent=agent, room=ctx.room)
    await session.say(call.greeting, allow_interruptions=False)


def main() -> None:
    _load_env()
    if not os.environ.get("ELEVEN_API_KEY") and "--list-devices" not in sys.argv:
        raise SystemExit("Set ELEVEN_API_KEY in .env: ElevenLabs does the listening and the "
                         "speaking.")
    if not (DATA / "clinic.db").exists():
        raise SystemExit(f"{DATA / 'clinic.db'} not found; run `vetdesk generate` first.")
    cli.run_app(server)
