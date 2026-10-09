"""The front desk on a voice line, through LiveKit Agents.

LiveKit does the hard real-time part: listening, deciding when the caller has finished,
letting them interrupt, playing the answer. ElevenLabs hears (Scribe) and speaks. The
thinking is not theirs: every answer comes from `FrontDeskAgent`, the same one the
evaluation harness measures, reached through LiveKit's `llm_node`.

Two ways to run it:

    uv run python -m vetdesk.voice console    # this computer's microphone and speakers
    uv run python -m vetdesk.voice start      # calls from a LiveKit project's rooms

In the console the agent lives in this program, and nothing else is needed. For real
rooms it does not: this program asks the voice server what to say (`desk_client`), where
the passes of the demo, the record of the call and when to hang up are already decided for
every other way a call comes in. It needs the project's address and keys (LIVEKIT_URL,
LIVEKIT_API_KEY, LIVEKIT_API_SECRET) and the voice server running on this machine.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from collections.abc import AsyncIterator, Callable
from datetime import datetime
from pathlib import Path

import aiohttp
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    JobProcess,
    cli,
    inference,
    llm,
)
from livekit.plugins import elevenlabs, silero

from ..agent import FrontDeskAgent
from ..agent.agent import Turn
from ..kb import load_kb
from ..legacy import LegacySqliteSource
from ..llm import create_client
from ..scheduling import SqliteAgenda
from .bridge import TROUBLE, Line, language_of
from .desk_client import DESK_URL, Asked, ask
from .livekit_demo import AGENT

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
# The first is the clinic's language; the others are what callers may speak instead.
# Only Catalan by default. With English, French, German, Dutch and Italian added, the
# recogniser turned a Spanish sentence said into a real microphone into Dutch, and the
# agent was handed "Hoi, ik wil een afspraak maken". Visitors' languages need another way
# in than a longer list.
STT_LANGUAGES = os.environ.get("VETDESK_STT_LANGUAGES", "es,ca").split(",")
# Seconds of silence after which the recogniser closes what the caller said. Left to the
# local voice detector, a phrase said into a real microphone stayed open for 20 seconds.
STT_PAUSE = float(os.environ.get("VETDESK_STT_PAUSE", "0.6"))
# A call from a room is closed after this long whatever is being said, as on the other
# platforms' demos: a little over the three minutes the voice server gives a call.
MAX_SECONDS = float(os.environ.get("VETDESK_DEMO_MAX_SECONDS", "200"))
# Seconds of nobody speaking before the caller is asked whether they are still there, and
# again before the line is given up: the voice server has the words for both.
QUIET_SECONDS = 15.0


class _ElsewhereLLM(llm.LLM):
    """LiveKit only answers a caller when the session has a model. Ours lives in
    `FrontDeskAgent` and is reached through `llm_node`; this one is never asked."""

    def chat(self, **kwargs):  # pragma: no cover - never called
        raise NotImplementedError("the front desk answers through llm_node")


class VoiceFrontDesk(Agent):
    """Ears and a mouth. What is said comes from `answer`, given the conversation so far;
    `asked.hang_up` says, once an answer is in, that the call ends when it has been said,
    and `close` ends it."""

    def __init__(self, answer: Callable[[list[dict]], AsyncIterator[str]], asked: Asked,
                 close: Callable[[], object]) -> None:
        # The instructions live with the agent that does the answering, not here.
        super().__init__(instructions="")
        self._answer, self._asked, self._close = answer, asked, close

    async def llm_node(self, chat_ctx, tools, model_settings) -> AsyncIterator[str]:
        messages = [{"role": item.role, "content": item.text_content}
                    for item in chat_ctx.items
                    if getattr(item, "role", None) in ("user", "assistant") and item.text_content]
        self._asked.hang_up = False
        async for piece in self._answer(messages):
            yield piece
        if self._asked.hang_up:
            # Only this program can put the phone down, and not over its own goodbye.
            asyncio.ensure_future(self.say_and_close(self.session.current_speech))

    async def say_and_close(self, speech) -> None:
        if speech is not None:
            await speech.wait_for_playout()
        log.info("the goodbyes are said: the line is closed")
        await self._close()


def _in_this_program(ready: dict, agent_language: Callable[[], str]):
    """The agent itself, for the console: no voice server, a hidden number, a book of
    appointments that lasts as long as the call."""
    kb = ready["kb"]
    front_desk = FrontDeskAgent(ready["llm"], ready["clinic"], kb,
                                SqliteAgenda(kb, datetime.now))
    # No caller ID on a microphone. Set VETDESK_CALLER_NUMBER to try a call "from" a number.
    call = front_desk.start_call(os.environ.get("VETDESK_CALLER_NUMBER") or None)
    line = Line(call)

    def turn_done(turn: Turn) -> None:
        for event in turn.events:
            log.info("tool %s %s -> %s", event.name,
                     json.dumps(event.arguments, ensure_ascii=False),
                     json.dumps(event.result, ensure_ascii=False)[:300])
        log.info("model: first words %.1f s, complete %.1f s (%s)", turn.first_words or 0,
                 turn.seconds, " + ".join(f"{s:.1f}" for s in turn.latencies))

    async def answer(messages: list[dict]) -> AsyncIterator[str]:
        heard = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        if not heard:
            yield call.greeting
            return
        async for piece in line.answer(heard, agent_language(), turn_done):
            yield piece

    return answer


def _at_the_voice_server(http: aiohttp.ClientSession, room: str, asked: Asked):
    """The agent where every other call finds it: asked over this machine's own network."""
    desk = os.environ.get(DESK_URL, "http://127.0.0.1:8013")
    key = os.environ.get("VETDESK_ENDPOINT_KEY", "")

    async def answer(messages: list[dict]) -> AsyncIterator[str]:
        started, first = time.perf_counter(), None
        try:
            async for piece in ask(http, desk, key, room, messages, asked):
                first = first if first is not None else time.perf_counter() - started
                yield piece
        except (aiohttp.ClientError, TimeoutError) as error:
            # The caller must never be left with nothing.
            log.warning("the voice server did not answer (%s)", type(error).__name__)
            if first is None:
                yield TROUBLE["es"]
        log.info("front desk: first words %.1f s, complete %.1f s", first or 0,
                 time.perf_counter() - started)

    return answer


def prewarm(proc: JobProcess) -> None:
    """Load what takes time before the phone rings, not while the caller waits."""
    _load_env()
    proc.userdata.update(vad=silero.VAD.load())


server = AgentServer(
    setup_fnc=prewarm,
    # One call waiting to be taken is enough for a demo, on a server it shares.
    num_idle_processes=int(os.environ.get("VETDESK_VOICE_IDLE", "1")),
)


@server.rtc_session(agent_name=AGENT)
async def entrypoint(ctx: JobContext) -> None:
    ready, asked = ctx.proc.userdata, Asked()
    language = ["es"]  # updated from what speech recognition hears
    http: aiohttp.ClientSession | None = None

    over = []  # anything in it: the line has been closed

    async def close() -> None:
        if over:
            return
        over.append(True)
        if ctx.is_fake_job():
            ctx.shutdown("the call is over")
            return
        try:
            await ctx.delete_room()  # everybody in it is disconnected
        except Exception as error:  # gone already: the caller left first
            log.info("the room was not closed from here (%s)", type(error).__name__)

    if ctx.is_fake_job():  # the console: the agent is loaded here, once, off the loop
        if "clinic" not in ready:
            ready.update(kb=load_kb(), llm=create_client(), clinic=await asyncio.to_thread(
                LegacySqliteSource(DATA / "clinic.db").load))
        answer = _in_this_program(ready, lambda: language[0])
    else:
        await ctx.connect()
        http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60))
        ctx.add_shutdown_callback(http.close)
        log.info("call in room %s", ctx.room.name)
        answer = _at_the_voice_server(http, ctx.room.name, asked)
    agent = VoiceFrontDesk(answer, asked, close)

    session = AgentSession(
        # With a primary language set, the recogniser stops saying which language it
        # heard unless asked to: it then called a Catalan sentence Spanish.
        stt=elevenlabs.STT(model=STT_MODEL, language_code=STT_LANGUAGES[0],
                           secondary_languages=STT_LANGUAGES[1:],
                           include_language_detection=True,
                           server_vad={"vad_silence_threshold_secs": STT_PAUSE}),
        llm=_ElsewhereLLM(),
        tts=elevenlabs.TTS(model=TTS_MODEL, **({"voice_id": TTS_VOICE} if TTS_VOICE else {})),
        vad=ready["vad"],
        turn_handling={
            # The small model that runs here, said by name. Left to choose, the library
            # takes LiveKit's hosted one whenever the project's keys are at hand, and the
            # same for telling an interruption from a noise: both paid for, and neither
            # the one this was tried with.
            "turn_detection": inference.TurnDetector(version="v1-mini"),
            # The agent stops for words, not for a sound. Left to stop at any half second
            # of sound, it stood still in the middle of a long sentence, waited two
            # seconds for words that did not come, and went on: heard on the first call
            # from a browser with a real microphone as a line that hangs. The same
            # sentence played into the same server with no microphone was said in one go.
            "interruption": {"mode": "vad", "min_words": 2},
            # LiveKit can start on an answer before it is sure the caller has finished,
            # and throw it away if they go on. Our agent's turns have effects (a booking),
            # so an answer is only asked for once the turn is over.
            "preemptive_generation": {"enabled": False},
        },
        user_away_timeout=QUIET_SECONDS,
    )
    heard_at = [time.monotonic()]

    @session.on("user_input_transcribed")
    def _heard(event) -> None:
        heard_at[0] = time.monotonic()
        if event.is_final:
            language[0] = language_of(event.language)
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

    def quiet() -> None:
        """Nobody has spoken for a while. The voice server is told as the first platform
        tells it, with a line of dots, and has the words: "are you still there?" the
        first time, and a goodbye that closes the line the second."""
        if over or time.monotonic() - heard_at[0] < QUIET_SECONDS - 1:
            return  # the call is over, or they spoke meanwhile
        try:
            session.generate_reply(user_input="...")
        except RuntimeError:  # the session has ended
            return
        asyncio.get_running_loop().call_later(QUIET_SECONDS + 5, quiet)

    @session.on("user_state_changed")
    def _gone_quiet(event) -> None:
        if event.new_state == "away" and not ctx.is_fake_job():
            quiet()

    @session.on("close")
    def _ended(event) -> None:
        # However it ended: a caller who left, or ears and a mouth that failed (a key
        # that may not hear or speak was heard as a line that picks up and says nothing).
        # Nobody is left in a room that will not answer.
        if getattr(event, "error", None):
            log.error("the call ended on an error: %s", event.error)
        asyncio.ensure_future(close())

    await session.start(agent=agent, room=ctx.room)
    if not ctx.is_fake_job():
        asyncio.get_running_loop().call_later(MAX_SECONDS, lambda: asyncio.ensure_future(close()))
    # The greeting is the front desk's too: a call the voice server has no pass for is
    # told so in its place, and closed.
    greeting = "".join([piece async for piece in answer([])])
    speech = session.say(greeting, allow_interruptions=False)
    if asked.hang_up:
        await agent.say_and_close(speech)


def main() -> None:
    _load_env()
    if not os.environ.get("ELEVEN_API_KEY") and "--list-devices" not in sys.argv:
        raise SystemExit("Set ELEVEN_API_KEY in .env: ElevenLabs does the listening and the "
                         "speaking.")
    if "console" in sys.argv and not (DATA / "clinic.db").exists():
        raise SystemExit(f"{DATA / 'clinic.db'} not found; run `vetdesk generate` first.")
    # What the caller said, what the agent said and how long it took. Not every packet.
    os.environ.setdefault("LIVEKIT_LOG_LEVEL", "INFO")
    for noisy in ("httpcore2", "httpx2", "httpcore", "httpx", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    cli.run_app(server)
