"""The agent behind a chat-completions address, for a voice platform that brings its own
ears and mouth.

ElevenLabs Agents can be told to use a "custom LLM": an address that speaks the OpenAI
chat-completions format. ElevenLabs then does the listening, the turn-taking, the
interruptions, the voice and the phone line, and asks this address what to say. What
answers here is `FrontDeskAgent`, the agent the evaluation harness measures: its prompt,
its tools and its privacy barrier, not a model with a prompt in somebody's dashboard.

    uv run python -m vetdesk.voice.endpoint            # listens on port 8013

The platform resends the whole conversation with every request; the agent keeps its own.
So each request is matched to its call, and only the caller's last line is taken from it.
The agent set up on the platform needs nothing but these two lines as its prompt, which
tell this address which call a request belongs to and who is calling:

    vetdesk-conversation: {{system__conversation_id}}
    vetdesk-caller: {{system__caller_id}}

The address has to be reachable from the internet, so every request must carry the key in
VETDESK_ENDPOINT_KEY as a bearer token. Without the key nothing is answered.

The platform listens in one language at a time. Set to Spanish, its recogniser wrote a
caller's Catalan as Spanish ("dos quarts de cinc" arrived as "a dos cuartos de cinco"), and
the agent, reading Spanish, answered in Spanish. It offers a tool to change the language it
listens and speaks in, meant for its own model to call. Here the language is worked out in
code from the caller's words, so it is this address that calls it: the first line that
tells another language is answered with that tool call and nothing else, the platform
changes language and asks again, and then it gets the answer.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from collections import deque
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from aiohttp import web

from ..agent import FrontDeskAgent
from ..agent.agent import Call, Turn
from ..agent.prompt import THROUGH
from ..evals.cost import cost
from ..identity.resolver import MISTYPED_ON_FILE
from ..kb import KnowledgeBase, load_kb, why_not
from ..kb.model import spoken_phone
from ..language import SPOKEN
from ..legacy import LegacySqliteSource
from ..legacy.normalize import parse_phones
from ..llm import create_client
from ..notices import Notice
from ..scenario import load_jsonl
from ..scheduling import AgendaError, Appointment, SqliteAgenda
from ..scheduling.google_calendar import CALENDAR, GoogleCalendar, MirroredAgenda, session_from
from ..scheduling.google_calendar import KEY as CALENDAR_KEY
from ..spoken import say_en, say_es
from .bridge import TROUBLE, Line, is_goodbye, may_end, only_closes, says_no_more, silence
from .call_log import FILE as CALLS_FILE
from .call_log import KEEP_DAYS as CALLS_KEEP_DAYS
from .call_log import PAGE as CALLS_PAGE
from .call_log import CallLog
from .clinic_file import PAGE, ClinicFile
from .demo import (
    NO_PASS,
    TIME_IS_UP,
    Demo,
    Full,
    Persona,
    personas,
    slot_ahead,
    with_an_appointment,
)
from .demo_page import PAGE as DEMO_PAGE
from .desk_client import DESK_URL
from .desk_client import PLATFORM as LIVEKIT
from .livekit_demo import KEY as LIVEKIT_KEY
from .livekit_demo import SECRET as LIVEKIT_SECRET
from .livekit_demo import URL as LIVEKIT_URL
from .livekit_demo import way_in
from .telegram import CHAT as TELEGRAM_CHAT
from .telegram import TOKEN as TELEGRAM_TOKEN
from .telegram import Telegram
from .token import platform_key
from .vapi import AGENT_ID as VAPI_AGENT
from .vapi import END_TOOL as VAPI_END_TOOL
from .vapi import FOR_THE_PAGE, VAPI_KEY, say_and_hang_up, starter

log = logging.getLogger("vetdesk.endpoint")

KEY_NAME = "VETDESK_ENDPOINT_KEY"
STAND_INS = "VETDESK_CALLER_STANDS_IN_FOR"
AGENDA_FILE = "VETDESK_AGENDA"
CLINIC_FILE, ADMIN_KEY = "VETDESK_CLINIC", "VETDESK_ADMIN_KEY"
# The public demo: its agent on the voice platform, the key to ask that platform for the
# address of each call, and how many minutes it may use.
DEMO_AGENT, VOICE_KEY = "VETDESK_ELEVENLABS_DEMO_AGENT_ID", "ELEVEN_API_KEY"
DEMO_MINUTES_A_DAY = "VETDESK_DEMO_MINUTES_A_DAY"
DEMO_MINUTES_A_CALL = "VETDESK_DEMO_MINUTES_A_CALL"
IDLE_SECONDS = 30 * 60  # a call nobody has asked about for this long is over
# ElevenLabs wraps the agent's prompt in text of its own, so the two markers are looked for
# anywhere in it, under names nothing else would use.
_CONVERSATION = re.compile(r"vetdesk-conversation:\s*(\S+)")
_CALLER = re.compile(r"vetdesk-caller:[ \t]*([+\d][\d ()-]*)?")
# The pass of a call made from the public demo's page: see `demo`. A platform agent that
# sends this line is the demo's, and a call of its without a good pass is not answered.
_DEMO = re.compile(r"vetdesk-demo:[ \t]*(\S*)")
# The platform's tool for changing the language it listens and speaks in, and the language
# its agent is set up to start in (see `elevenlabs_agent`).
LANGUAGE_TOOL, FIRST_LANGUAGE = "language_detection", "es"
# The platform's tool for hanging up. Our address can say goodbye; only the platform can
# put the phone down.
END_TOOL = "end_call"
# The platform's tool for putting a call through to a phone number, and the setting that
# holds the number a person answers at. Without the number nothing is ever put through.
TRANSFER_TOOL, TRANSFER_TO = "transfer_to_number", "VETDESK_TRANSFER_TO"
# Told to the model when the platform could not put the call through after all.
NOT_PUT_THROUGH = ("(Note from the phone system, not from the caller: the call could not be "
                   "put through, nobody picked up. Tell the caller so, in a few words, and "
                   "offer to take a message with take_message.)")


def _text(content) -> str:
    """A message's text, whether it came as a string or as a list of parts."""
    if isinstance(content, str):
        return content
    return " ".join(part.get("text", "") for part in content or [] if isinstance(part, dict))


def _opening(messages: list[dict]) -> str:
    """What the caller heard when the phone was picked up, as far as the platform tells us.

    The platform answers the phone with a first message of its own and passes it on as
    what the assistant said before the caller spoke. Empty when it passes nothing.
    """
    for message in messages:
        if message.get("role") == "user":
            break
        if message.get("role") == "assistant" and (text := _text(message.get("content")).strip()):
            return text
    return ""


class Switchboard:
    """Which call each request belongs to. One `Line` per conversation."""

    def __init__(self, start_call: Callable[[str | None], Call],
                 clock: Callable[[], float] = time.monotonic,
                 stand_ins: dict[str, str] | None = None, demo: Demo | None = None,
                 start_demo_call: Callable[[Persona | None], Call] | None = None) -> None:
        self._start_call, self._clock = start_call, clock
        # The public demo's passes, and how one of its calls is started, given who the
        # visitor calls as (nobody, with no pass): with an appointment book of its own,
        # so that it touches nothing a real call would.
        self._demo = demo
        self._start_demo_call = start_demo_call or (
            lambda persona: start_call(persona.caller_number if persona else None))
        self._demo_lines: dict[str, Line] = {}  # by pass, for the page to ask what happened
        self._lines: dict[str, tuple[Line, float]] = {}
        # Real numbers that call as a number of the made-up clinic: see `stand_ins`.
        self._stand_ins = stand_ins or {}

    def demo_line(self, token: str) -> Line | None:
        """The call a pass of the public demo was used for, while it is remembered."""
        return self._demo_lines.get(token)

    def line(self, messages: list[dict], can_transfer: bool = False,
             named: str | None = None, demo_pass: str = "") -> Line:
        """`named` is for a platform that says which call a request is for outside what
        is said (see `vapi`). Nothing is then read from the system text: the call is the
        demo's, with `demo_pass` for its pass."""
        system = "" if named else " \n".join(
            _text(m.get("content")) for m in messages if m.get("role") == "system")
        found = _CONVERSATION.search(system)
        if named:
            conversation = named
        elif found and "{{" not in found.group(1):
            conversation = found.group(1)
        else:
            # No id from the platform: the opening of a conversation never changes, so it
            # serves as one. Two calls that open with the same words would be confused,
            # which is why the two prompt lines above matter.
            opening = json.dumps(messages[:2], sort_keys=True, ensure_ascii=False)
            conversation = "opening-" + hashlib.sha256(opening.encode()).hexdigest()[:16]
        now = self._clock()
        self._lines = {k: v for k, v in self._lines.items() if now - v[1] < IDLE_SECONDS}
        from_demo = _DEMO.search(system)
        token = demo_pass if named else from_demo.group(1) if from_demo else None
        if conversation not in self._lines and token is not None:
            # The number is the one of whoever the visitor chose to call as. With no pass,
            # or one that is not good, the line exists only to be told so and closed.
            given = self._demo.call(token) if self._demo else None
            log.info("call %s from the demo, %s", conversation,
                     f"as '{given.persona.key}'" if given else "with no pass")
            line = Line(self._start_demo_call(given.persona if given else None), patience=None)
            line.listening_in, line.name = FIRST_LANGUAGE, conversation
            line.demo = token if given else ""
            self._lines[conversation] = (line, now)
            if given:
                going = {id(kept) for kept, _ in self._lines.values()}
                self._demo_lines = {token: kept for token, kept in self._demo_lines.items()
                                    if id(kept) in going}
                self._demo_lines[line.demo] = line
        elif token is not None and self._demo and self._lines[conversation][0].demo:
            self._demo.call(self._lines[conversation][0].demo)  # heard of again
        if conversation not in self._lines:
            caller = _CALLER.search(system)
            numbers, _ = parse_phones((caller.group(1) if caller else "") or "")
            number = numbers[0] if numbers else None
            if number in self._stand_ins:
                number = self._stand_ins[number]
                log.info("call %s from a number that stands in for %s", conversation, number)
            else:
                log.info("call %s from %s", conversation, number or "a hidden number")
            if not found:
                log.info("no conversation id in what the platform sent as system text: %r",
                         system)
            # No waiting phrase of ours on this route. The platform holds back whatever it
            # is sent until more text follows, so the phrase never covered a wait: it was
            # spoken with the answer, in front of it. The platform's own filler does the
            # job (the soft timeout set in `elevenlabs_agent`).
            call = self._start_call(number, True) if can_transfer else \
                self._start_call(number)
            line = Line(call, patience=None)
            line.listening_in, line.name = FIRST_LANGUAGE, conversation
            self._lines[conversation] = (line, now)
        line, _ = self._lines[conversation]
        self._lines[conversation] = (line, now)
        return line


def what_happened(line: Line) -> dict:
    """What the agent decided on a demo call, for the page to show when it is over.

    From the page a caller who was not confirmed and one who was look alike: both ask for
    an appointment and get one. The difference is in what the agent took them for and in
    what reception is told, and neither is heard on the call.
    """
    session = line.call.session
    found = session.resolution
    if session.client is not None:
        winner = found.candidates[0] if found and found.candidates else None
        identity = {"level": "confirmed", "name": session.client.raw_name,
                    "by": "phone" if winner is not None and winner.phone_on_file else "pet_town",
                    "mistyped": bool(found and found.why.startswith(MISTYPED_ON_FILE))}
    elif found is None:
        identity = {"level": "none", "why": "not_asked"}
    elif found.decision == "not_found":
        identity = {"level": "none", "why": "not_a_client"}
    elif "another client's record" in found.why:
        identity = {"level": "none", "why": "other_phone"}
    else:
        identity = {"level": "none", "why": "not_enough"}
    return {"identity": identity, "reception": [notice.text for notice in line.told]}


class Desk:
    """The front desk as it is now: made again whenever the clinic's information changes.

    Calls already going on keep the information they started with; the next call gets the
    new one, and the agenda its new opening hours.
    """

    def __init__(self, make: Callable[[KnowledgeBase], FrontDeskAgent], agenda,
                 kb: KnowledgeBase, file: ClinicFile | None = None,
                 make_demo: Callable[[KnowledgeBase, Persona | None], FrontDeskAgent] | None
                 = None) -> None:
        self._make, self._agenda, self.file = make, agenda, file
        self._agent = make(kb)
        # For the public demo: an agent made for one call, as whoever the visitor chose.
        self._make_demo, self.kb = make_demo or (lambda kb, persona: make(kb)), kb

    @property
    def agenda(self):
        """The appointment book, for the clinic's own look at it: see `/agenda`."""
        return self._agenda

    def start_call(self, number: str | None, can_transfer: bool = False) -> Call:
        return self._agent.start_call(number, can_transfer)

    def start_demo_call(self, persona: Persona | None) -> Call:
        """A call from the demo's page: never put through to anybody."""
        return self._make_demo(self.kb, persona).start_call(
            persona.caller_number if persona else None)

    def replace(self, text: str) -> None:
        kb = self.file.replace(text)  # raises, and changes nothing, if the text is wrong
        self._agent, self.kb = self._make(kb), kb
        self._agenda.follow(kb)


def _offers(body: dict, name: str) -> bool:
    """Whether the platform has handed this tool of its own over with the request."""
    return any((tool.get("function") or {}).get("name") == name
               for tool in body.get("tools") or [] if isinstance(tool, dict))


def _change_of_language(body: dict, messages: list[dict], line: Line) -> str | None:
    """The language to tell the platform to change to before this line is answered."""
    if not _offers(body, LANGUAGE_TOOL) or messages[-1].get("role") != "user":
        return None  # no such tool, or it has just been used and the answer is due
    spoken = line.call.hears(_text(messages[-1].get("content")))
    return spoken if spoken in SPOKEN and spoken != line.listening_in else None


async def _run(pieces) -> None:
    async for _ in pieces:
        pass


def stand_ins(setting: str) -> dict[str, str]:
    """Real phone numbers that are to count as numbers of the made-up clinic.

    Every record in the clinic is invented, so nobody's real phone is on file, and the
    usual call (from the number on one's record) could never be tried by voice. A setting
    on the server, never in this repository, says which real number calls as which of the
    clinic's: "real=clinic's, real=clinic's". The records stay made up.
    """
    pairs = {}
    for pair in filter(None, (part.strip() for part in setting.split(","))):
        real, _, pretend = pair.partition("=")
        (real_numbers, _), (pretend_numbers, _) = parse_phones(real), parse_phones(pretend)
        if not real_numbers or not pretend_numbers:
            raise SystemExit(f"{STAND_INS} holds something that is not 'number=number'")
        pairs[real_numbers[0]] = pretend_numbers[0]
    return pairs


def _chunk(request_id: str, model: str, delta: dict, finish: str | None = None) -> bytes:
    body = {"id": request_id, "object": "chat.completion.chunk", "created": int(time.time()),
            "model": model, "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
    return f"data: {json.dumps(body, ensure_ascii=False)}\n\n".encode()


# What is kept, as it is, of a request whose shape is being looked at: the names of things.
_STRUCTURAL = ("role", "type", "name", "provider", "model", "status", "object")


def shape(value, key: str = ""):
    """The form of what a platform sent, with nothing of what was said in it: the keys,
    and for a value only what kind of thing it is. The names of roles, tools and
    providers are kept; a caller's words and their number are not."""
    if isinstance(value, dict):
        return {name: shape(inner, name) for name, inner in value.items()}
    if isinstance(value, list):
        return [shape(inner, key) for inner in value[:12]] + (
            [f"... {len(value) - 12} more"] if len(value) > 12 else [])
    if isinstance(value, str):
        return value if key in _STRUCTURAL and len(value) <= 40 else f"str({len(value)})"
    return type(value).__name__


def build_app(switchboard: Switchboard, key: str, model: str = "", desk: Desk | None = None,
              admin_key: str = "",
              tell: Callable[[Notice], None] | None = None,
              transfer_to: str = "", calls: CallLog | None = None,
              demo: Demo | None = None, sign: Callable[[], str] | None = None,
              demo_page: str = "",
              vapi_call: Callable[[], dict] | None = None,
              vapi_say: Callable[[str, str], bool] | None = None,
              livekit_join: Callable[[str], dict] | None = None) -> web.Application:
    """`model` is the agent's own model, named only to put a price on each answer. With a
    `desk` whose information is in a file and an `admin_key`, that information can be read
    and replaced through the server. `tell` is how reception is told what happens on a
    call: see `notices`. `transfer_to` is the number a person answers at, to put calls
    through to. `calls` is where every call is written down, to be read back with the
    `admin_key`: see `call_log`. `demo` holds the public demo's passes, and `sign` asks
    the voice platform for the address one browser call is made at: see `demo`.
    `vapi_call` starts a browser call on the second platform, and `vapi_say` has one of
    its calls say something and end when it has been said: see `vapi`. `livekit_join` is
    how a browser gets into a room of the third way to carry a call: see `livekit_demo`."""
    agent_model = model
    put_through: set[int] = set()  # the lines the platform has been told to put through
    closed: set[int] = set()  # the lines a platform has been told to say goodbye on and end
    waiting: set[asyncio.Future] = set()  # answers being worked out for a request to come

    def spent(turn: Turn) -> None:
        usage, price = turn.usage, cost(agent_model, turn.usage)
        log.info("model: %d requests, %d tokens in (%d from cache), %d out%s", turn.requests,
                 usage.input_tokens + usage.cache_read_tokens + usage.cache_write_tokens,
                 usage.cache_read_tokens, usage.output_tokens,
                 f", ${price:.4f}" if price is not None else "")

    def finished(line: Line, heard: str, number: int | None = None,
                 note: str | None = None) -> Callable[[Turn], None]:
        """What to do when a turn is over, whether or not anybody is still listening."""
        def after(turn: Turn) -> None:
            spent(turn)
            session = line.call.session
            if calls:
                found = session.resolution
                calls.turn(line.name, number, heard, turn, cost(agent_model, turn.usage),
                           session.language, found.level if found else "none",
                           session.client.raw_name if session.client else None, note)
            waiting_for_reception = session.notices
            while waiting_for_reception:
                notice = waiting_for_reception.pop(0)
                log.info("for reception: %s", notice.kind)
                if calls:
                    calls.happened(line.name, notice.kind)
                if line.demo is not None:  # a demo call is nobody reception serves:
                    line.told.append(notice)  # its page shows what would have been told
                elif tell:
                    tell(notice)
        return after

    async def use(response: web.StreamResponse, request_id: str, model: str, tool: str,
                  say: str = "", **arguments: str) -> web.StreamResponse:
        """Answer with a call to one of the platform's own tools, and, with `say`, words
        for the platform to speak before it runs the tool."""
        if say:
            await response.write(_chunk(request_id, model, {"content": say}))
        call = {"index": 0, "id": "call_" + secrets.token_hex(8), "type": "function",
                "function": {"name": tool, "arguments": json.dumps(arguments,
                                                                   ensure_ascii=False)}}
        await response.write(_chunk(request_id, model, {"tool_calls": [call]}))
        await response.write(_chunk(request_id, model, {}, "tool_calls"))
        await response.write(b"data: [DONE]\n\n")
        await response.write_eof()
        return response

    async def chat_completions(request: web.Request) -> web.StreamResponse:
        given = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        if not key or not hmac.compare_digest(given.encode(), key.encode()):
            return web.json_response({"error": {"message": "invalid key"}}, status=401)
        try:
            body = await request.json()
            messages = list(body["messages"])
        except (ValueError, KeyError, TypeError):
            return web.json_response({"error": {"message": "expected chat messages"}},
                                     status=400)
        line = switchboard.line(messages, bool(transfer_to) and _offers(body, TRANSFER_TOOL))
        return await converse(request, body, messages, line, END_TOOL)

    async def converse(request: web.Request, body: dict, messages: list[dict], line: Line,
                       end_tool: str,
                       say_and_end: Callable[[str], bool] | None = None) -> web.StreamResponse:
        """Answer one request of a call. `end_tool` is the platform's name for hanging up.
        `say_and_end` is for a platform whose tool hangs up over what is being said: a way
        of having it say the last words and end when they have been said."""
        said = [_text(m.get("content")) for m in messages if m.get("role") == "user"]
        if calls and line.on_taken_back is None:  # the first that is heard of this call
            # The greeting written down is the one the caller heard: the platform's own,
            # or ours when it asks us to open the call. Neither known, none is written.
            calls.begin(line.name, line.call.session.caller_number, agent_model,
                        _opening(messages) or ("" if said else line.call.greeting))
            line.on_taken_back = lambda number: calls.taken_back(line.name, number)
            if line.demo is not None:
                calls.note(line.name, "demo")
        request_id = "chatcmpl-" + secrets.token_hex(8)
        model = str(body.get("model", "vetdesk"))

        response = web.StreamResponse(headers={"Content-Type": "text/event-stream",
                                               "Cache-Control": "no-cache"})
        await response.prepare(request)
        started = time.perf_counter()

        async def hang_up(text: str, reason: str) -> web.StreamResponse:
            """The last words, and the line closed. Words first: said by us ahead of the
            platform's tool, or, where that tool cuts them short, by the platform itself
            when it is told to say them and end."""
            told = id(line) in closed or (say_and_end is not None and
                                          await asyncio.to_thread(say_and_end, text))
            if not told:
                return await use(response, request_id, model, end_tool, say=text,
                                 reason=reason)
            closed.add(id(line))  # asked again for this answer, it is not said again
            await response.write(_chunk(request_id, model, {}, "stop"))
            await response.write(b"data: [DONE]\n\n")
            await response.write_eof()
            return response

        if line.demo is not None and (not line.demo or demo is None or demo.over(line.demo)):
            # A demo call with no pass, or one that has run its time: told so, and closed.
            # No model is asked anything, so it costs no more than the words.
            text = TIME_IS_UP[line.call.session.language] if line.demo else NO_PASS
            log.info("demo call %s: %s", line.name, "time is up" if line.demo else "no pass")
            if calls:
                calls.said(line.name, text, "demo_over" if line.demo else "demo_refused")
            await response.write(_chunk(request_id, model, {"role": "assistant", "content": ""}))
            if _offers(body, end_tool) and (not messages
                                            or messages[-1].get("role") != "tool"):
                return await hang_up(text, "the demo call is over")
            await response.write(_chunk(request_id, model, {"content": text}))
            await response.write(_chunk(request_id, model, {}, "stop"))
            await response.write(b"data: [DONE]\n\n")
            await response.write_eof()
            return response
        try:
            await response.write(_chunk(request_id, model, {"role": "assistant", "content": ""}))
            if not said:  # asked to open the call: the agent's own greeting
                await response.write(_chunk(request_id, model, {"content": line.call.greeting}))
            elif language := _change_of_language(body, messages, line):
                # Tell the platform first, and say nothing: it changes language and asks
                # again. The answer is worked out meanwhile, so it is ready when it does.
                log.info("caller: %s", said[-1])
                log.info("the caller speaks %s and the platform listens in %s: told to change",
                         language, line.listening_in)
                line.listening_in = language
                if calls:
                    calls.note(line.name, f"language:{language}")
                waiting.add(task := asyncio.ensure_future(_run(line.answer(
                    said[-1], on_turn=finished(line, said[-1], len(said)), turn=len(said)))))
                task.add_done_callback(waiting.discard)
                return await use(response, request_id, model, LANGUAGE_TOOL,
                                 reason="the caller is speaking this language",
                                 language=language)
            else:
                after_a_tool = messages[-1].get("role") == "tool"
                heard, turn, note = said[-1], len(said), False
                if after_a_tool:
                    result = _text(messages[-1].get("content"))
                    log.info("the platform answered a tool of its own: %s", result[:200])
                    if id(line) in put_through and "error" in result.lower():
                        # Nobody picked up. The caller is still with us and the model
                        # believes they are gone: it is told, and takes it from there.
                        put_through.discard(id(line))
                        line.call.session.transfer = None
                        heard, turn, note = NOT_PUT_THROUGH, None, True
                        if calls:
                            calls.note(line.name, "not_put_through")
                log.info("caller: %s", heard)
                # A call may be over after this line (nothing said, or a goodbye). Then
                # the answer is not said piece by piece but kept whole: if it is a
                # goodbye too, the platform is handed it to say and told to hang up.
                asked = next((_text(m.get("content")) for m in reversed(messages[:-1])
                              if m.get("role") == "assistant"), "")
                closing = _offers(body, end_tool) and not after_a_tool and (
                    may_end(said[-1]) or says_no_more(asked, said[-1]))
                session = line.call.session
                done_before = len(session.events)  # to tell whether this turn did anything
                answer, first = [], None
                on_turn = finished(line, "" if note else heard, turn,
                                   "not_put_through" if note else None)
                async for piece in line.answer(heard, on_turn=on_turn, turn=turn, note=note):
                    first = first if first is not None else time.perf_counter() - started
                    answer.append(piece)
                    # A model calls its tools before it speaks, so by its first words it
                    # is known whether this turn asked for the call to be put through. If
                    # it did, the platform says the words itself, with its own tool.
                    if not closing and not session.transfer:
                        await response.write(_chunk(request_id, model, {"content": piece}))
                text = "".join(answer)
                log.info("agent (first words %.1f s, all %.1f s): %s", first or 0,
                         time.perf_counter() - started, text)
                if calls and silence(heard) and not note:
                    calls.said(line.name, text, "silence")  # a stock phrase: no model
                elif calls and text in TROUBLE.values():
                    calls.said(line.name, text, "trouble", heard)  # the model failed
                if session.transfer and transfer_to and id(line) not in put_through:
                    put_through.add(id(line))
                    log.info("the caller wants a person: the platform is told to put the "
                             "call through")
                    if calls:
                        calls.note(line.name, "put_through")
                    # The words are ours to say, before the tool: on a call that came in
                    # over a SIP trunk the platform put it through, rightly, and did not
                    # say the message for the caller it had been handed.
                    return await use(response, request_id, model, TRANSFER_TOOL,
                                     say=THROUGH[session.language],
                                     reason="the caller asked to speak to a person",
                                     transfer_number=transfer_to,
                                     client_message=THROUGH[session.language],
                                     agent_message=session.transfer)
                # The caller's line was only their goodbye, and nothing was done in
                # answer to it: whatever the agent then says, short of a question, is its
                # own goodbye.
                done = only_closes(said[-1]) and len(session.events) == done_before
                if closing and is_goodbye(text, done):
                    log.info("the goodbyes are said: the platform is told to hang up")
                    if calls:
                        calls.note(line.name, "hung_up")
                    # Ours to say too, before the tool: handed to the tool as its
                    # farewell, on a phone call it was not said and the line just closed.
                    return await hang_up(text, "the caller and the agent have said goodbye")
                if closing:
                    await response.write(_chunk(request_id, model, {"content": text}))
            await response.write(_chunk(request_id, model, {}, "stop"))
            await response.write(b"data: [DONE]\n\n")
            await response.write_eof()
        except ConnectionResetError:
            # The platform stopped listening: the caller spoke over the answer, or it gave
            # up waiting and will ask again. The turn runs on; asked again, it is repeated.
            log.info("the platform hung up on this answer after %.1f s",
                     time.perf_counter() - started)
        return response

    async def health(request: web.Request) -> web.Response:
        return web.json_response({"status": "ok"})

    def may_edit(request: web.Request) -> bool:
        given = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        return bool(admin_key) and hmac.compare_digest(given.encode(), admin_key.encode())

    async def clinic(request: web.Request) -> web.Response:
        if not may_edit(request):
            return web.json_response({"error": "invalid key"}, status=401)
        if request.method == "GET":
            return web.Response(text=desk.file.text(), content_type="text/plain")
        try:
            desk.replace(await request.text())
        except Exception as error:  # not valid: nothing has changed
            return web.json_response({"error": why_not(error)}, status=400)
        log.info("the clinic's information was replaced")
        return web.json_response({"status": "saved"})

    async def edit(request: web.Request) -> web.Response:
        return web.Response(text=PAGE, content_type="text/html")

    # The record is read on a thread of its own: a slow disk must not hold up a call.
    def read_calls(limit: int) -> dict:
        calls.wait()
        return {"totals": calls.totals(), "calls": calls.calls(limit)}

    def read_call(call: str) -> dict | None:
        calls.wait()
        return calls.call(call)

    async def taken(request: web.Request) -> web.Response:
        if not may_edit(request):
            return web.json_response({"error": "invalid key"}, status=401)
        limit = request.query.get("limit", "")
        return web.json_response(await asyncio.to_thread(
            read_calls, int(limit) if limit.isdigit() else 200))

    async def one(request: web.Request) -> web.Response:
        if not may_edit(request):
            return web.json_response({"error": "invalid key"}, status=401)
        found = await asyncio.to_thread(read_call, request.match_info["call"])
        if found is None:
            return web.json_response({"error": "no such call"}, status=404)
        return web.json_response(found)

    async def view(request: web.Request) -> web.Response:
        return web.Response(text=CALLS_PAGE, content_type="text/html")

    # The appointment book, for the clinic and not for a caller: an appointment nobody was
    # confirmed for belongs to no record, so no call can cancel it. Reception can.
    def booked() -> list[dict]:
        return [{"id": a.appointment_id, "start": a.start.isoformat(timespec="minutes"),
                 "pet": a.pet_name, "reason": a.reason, "client": a.client_code,
                 "contact": a.contact_name, "phone": a.contact_phone, "verified": a.verified}
                for a in desk.agenda.all() if a.status == "booked"]

    async def appointments(request: web.Request) -> web.Response:
        if not may_edit(request):
            return web.json_response({"error": "invalid key"}, status=401)
        return web.json_response({"appointments": await asyncio.to_thread(booked)})

    async def cancel(request: web.Request) -> web.Response:
        if not may_edit(request):
            return web.json_response({"error": "invalid key"}, status=401)
        name = request.match_info["appointment"]
        try:
            await asyncio.to_thread(desk.agenda.cancel, name)
        except AgendaError:
            return web.json_response({"error": "no such appointment"}, status=404)
        log.info("appointment %s was cancelled by the clinic", name)
        return web.json_response({"status": "cancelled"})

    def starts_with(persona: Persona) -> dict | None:
        """The appointment a call as this caller starts with, as the page says it."""
        start = demo.booked(persona)
        if start is None:
            return None
        return {"pet": persona.pets[0], "es": say_es(start), "en": say_en(start)}

    def people() -> dict:
        return {"people": [{"key": p.key, "name": p.name, "town": p.town, "pets": list(p.pets),
                            "phone": spoken_phone(p.caller_number) if p.caller_number else None,
                            "language": p.language, "appointment": starts_with(p)}
                           for p in demo.people.values()],
                "minutes_left": int(demo.left().total_seconds() // 60),
                "seconds_a_call": int(demo.limit.total_seconds()),
                # The voice platforms a call can be carried by, for the page to offer.
                "platforms": ["elevenlabs"] + (["vapi"] if vapi_call else [])
                + ([LIVEKIT] if livekit_join else [])}

    async def demo_people(request: web.Request) -> web.Response:
        return web.json_response(people())

    async def demo_call(request: web.Request) -> web.Response:
        try:
            asked = await request.json()
            wanted, platform = str(asked["as"]), str(asked.get("platform") or "elevenlabs")
        except (ValueError, KeyError, TypeError, AttributeError):
            return web.json_response({"error": "expected who to call as"}, status=400)
        if platform not in people()["platforms"]:
            return web.json_response({"error": "no such voice platform"}, status=404)
        # Behind a proxy the visitor's address is the first it was forwarded for.
        address = (request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
                   or request.remote or "")
        try:
            token = demo.start(wanted, address)
        except KeyError:
            return web.json_response({"error": "nobody to call as by that name"}, status=404)
        except Full as full:
            log.info("demo: no call given (%s)", full.why)
            return web.json_response({"error": "full", "why": full.why}, status=429)
        if platform == "vapi":
            # The call itself is started when the platform's browser library asks for it,
            # with this pass: see `vapi_web_call`.
            return web.json_response({"pass": token, "platform": platform,
                                      "seconds": int(demo.limit.total_seconds())})
        if platform == LIVEKIT:
            # A room of its own, named here: the name is what ties the call to its pass.
            for old in [room for room, known in livekit_rooms.items() if demo.over(known)]:
                del livekit_rooms[old]
            room = "demo-" + secrets.token_urlsafe(9)
            livekit_rooms[room] = token
            return web.json_response({"pass": token, "platform": platform,
                                      "seconds": int(demo.limit.total_seconds()),
                                      **livekit_join(room)})
        try:
            address_to_call = await asyncio.to_thread(sign)
        except Exception as error:  # the platform would not: the minutes are given back
            demo.forget(token)
            log.warning("demo: the voice platform gave no address (%s)", type(error).__name__)
            return web.json_response({"error": "the voice platform is not answering"},
                                     status=503)
        return web.json_response({"signed_url": address_to_call, "pass": token,
                                  "seconds": int(demo.limit.total_seconds())})

    async def demo_result(request: web.Request) -> web.Response:
        line = switchboard.demo_line(request.query.get("pass", ""))
        if line is None:
            return web.json_response({"error": "no such call"}, status=404)
        return web.json_response(what_happened(line))

    async def demo_view(request: web.Request) -> web.Response:
        return web.Response(text=demo_page, content_type="text/html")

    # A second voice platform, Vapi, asked for to compare it with the first. Every call
    # by it is a call of the demo: one our server did not start has no pass, and is told
    # so and closed. The form of its last requests is kept (never their words) to be read
    # with the clinic's key: what it sends is not all in its documentation.
    seen: deque[dict] = deque(maxlen=8)
    # The call Vapi started for each pass of the demo, by the id it gave it; None while
    # it is being started. A pass starts one call.
    vapi_calls: dict[str, str | None] = {}

    async def vapi_web_call(request: web.Request) -> web.Response:
        """Start the browser call a pass of the demo is for. Vapi's browser library is
        told to ask here instead of Vapi, and sends the pass where its key would go."""
        token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        if not token or demo.over(token) or token in vapi_calls:
            return web.json_response({"error": "no pass for a call"}, status=401)
        for old in [known for known in vapi_calls if demo.over(known)]:
            del vapi_calls[old]
        vapi_calls[token] = None
        try:
            started = await asyncio.to_thread(vapi_call)
            vapi_calls[token] = str(started["id"])
        except Exception as error:  # the platform would not: the minutes are given back
            demo.forget(token)
            del vapi_calls[token]
            log.warning("demo: the second voice platform started no call (%s)",
                        type(error).__name__)
            return web.json_response({"error": "the voice platform is not answering"},
                                     status=503)
        return web.json_response({name: started[name] for name in FOR_THE_PAGE
                                  if name in started}, status=201)

    async def vapi(request: web.Request) -> web.StreamResponse:
        given = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        try:
            body = await request.json()
        except ValueError:
            body = None
        its_key = platform_key(key, "vapi") if key else ""
        sent = request.headers.get("Authorization", "")
        seen.append({"authorized": bool(key) and hmac.compare_digest(given.encode(),
                                                                     its_key.encode()),
                     # What came as a key, without the key: how it is put, how long it
                     # is, and a mark to tell it from another by.
                     "authorization": {"scheme": sent.partition(" ")[0][:12], "length": len(given),
                                       "mark": hashlib.sha256(given.encode()).hexdigest()[:8]},
                     "headers": sorted(name.lower() for name in request.headers),
                     "body": shape(body)})
        if not seen[-1]["authorized"]:
            return web.json_response({"error": {"message": "invalid key"}}, status=401)
        try:
            messages, call = list(body["messages"]), str(body["call"]["id"])
        except (KeyError, TypeError):
            return web.json_response({"error": {"message": "expected chat messages and "
                                                           "the call they are for"}}, status=400)
        # Which call this is, and so whose pass it has, is told by the id Vapi gave the
        # call when our server started it. Nothing in what is said is believed about it.
        token = next((known for known, started in vapi_calls.items() if started == call), "")
        line = switchboard.line(messages, named="vapi-" + call, demo_pass=token)
        # Its tool for hanging up cuts the farewell short: the call is told, at the
        # address Vapi sends to steer it by, to say it and end when it has.
        steer = ((body.get("call") or {}).get("monitor") or {}).get("controlUrl")
        return await converse(
            request, body, messages, line, VAPI_END_TOOL,
            (lambda text: vapi_say(str(steer), text)) if vapi_say and steer else None)

    # The third way: LiveKit, where the program that listens and speaks is ours and asks
    # here like a platform would. The pass of the demo each room was opened for.
    livekit_rooms: dict[str, str] = {}

    async def livekit(request: web.Request) -> web.StreamResponse:
        given = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        its_key = platform_key(key, LIVEKIT) if key else ""
        if not its_key or not hmac.compare_digest(given.encode(), its_key.encode()):
            return web.json_response({"error": {"message": "invalid key"}}, status=401)
        try:
            body = await request.json()
            messages, room = list(body["messages"]), str(body["call"]["id"])
        except (ValueError, KeyError, TypeError):
            return web.json_response({"error": {"message": "expected chat messages and "
                                                           "the call they are for"}}, status=400)
        # Every call by it is the demo's: a room this server did not name has no pass.
        line = switchboard.line(messages, named=f"{LIVEKIT}-{room}",
                                demo_pass=livekit_rooms.get(room, ""))
        return await converse(request, body, messages, line, END_TOOL)

    async def vapi_seen(request: web.Request) -> web.Response:
        if not may_edit(request):
            return web.json_response({"error": "invalid key"}, status=401)
        return web.json_response(list(seen))

    app = web.Application()
    app.router.add_post("/vapi/chat/completions", vapi)
    app.router.add_post(f"/{LIVEKIT}/chat/completions", livekit)
    if admin_key:
        app.router.add_get("/vapi/seen", vapi_seen)
    if demo is not None and sign is not None:
        app.router.add_get("/demo/people", demo_people)
        app.router.add_post("/demo/call", demo_call)
        if vapi_call:
            app.router.add_post("/demo/vapi/call/web", vapi_web_call)
        app.router.add_get("/demo/result", demo_result)
        if demo_page:
            app.router.add_get("/demo", demo_view)
    app.router.add_post("/v1/chat/completions", chat_completions)
    app.router.add_post("/chat/completions", chat_completions)
    app.router.add_get("/health", health)
    if desk is not None and desk.file is not None and admin_key:
        app.router.add_get("/clinic", clinic)
        app.router.add_put("/clinic", clinic)
        app.router.add_get("/clinic/edit", edit)
    if desk is not None and admin_key:
        app.router.add_get("/agenda", appointments)
        app.router.add_delete("/agenda/{appointment}", cancel)
    if calls is not None and admin_key:
        app.router.add_get("/calls", taken)
        app.router.add_get("/calls/view", view)
        app.router.add_get("/calls/{call}", one)
    return app


def _load_env(path: Path = Path(".env")) -> None:
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            name, separator, value = line.strip().partition("=")
            if separator and name and not name.startswith("#"):
                os.environ.setdefault(name.strip(), value.strip().strip("'\""))


def _key(host: str, path: Path = Path(".env")) -> str:
    """The key requests must carry. Made on first use and kept in .env, never printed."""
    if not os.environ.get(KEY_NAME) and host not in ("127.0.0.1", "localhost"):
        # On a server the key comes from its settings. One invented here would be known
        # to nobody, and one written into an image would be known to everybody.
        raise SystemExit(f"{KEY_NAME} is not set; refusing to listen on {host} without it.")
    if not os.environ.get(KEY_NAME):
        os.environ[KEY_NAME] = secrets.token_urlsafe(32)
        with path.open("a", encoding="utf-8") as env:
            env.write(f"\n{KEY_NAME}={os.environ[KEY_NAME]}\n")
        print(f"A new key was written to {path} as {KEY_NAME}.")
    return os.environ[KEY_NAME]


def _demo(data: Path, clinic) -> tuple[Demo | None, Callable[[], str] | None]:
    """The public demo, when it is set up: its agent on the voice platform, a key to ask
    that platform for the address of each call, and the clinic's test callers to call as."""
    agent, key = os.environ.get(DEMO_AGENT), os.environ.get(VOICE_KEY)
    scenarios = data / "scenarios.jsonl"
    if not (agent and key and scenarios.exists()):
        log.info("the public demo is off: it needs %s, %s and the test callers",
                 DEMO_AGENT, VOICE_KEY)
        return None, None
    demo = Demo(personas(load_jsonl(scenarios.read_text(encoding="utf-8")), clinic),
                minutes_a_day=float(os.environ.get(DEMO_MINUTES_A_DAY, "60")),
                minutes_a_call=float(os.environ.get(DEMO_MINUTES_A_CALL, "3")))
    url = ("https://api.elevenlabs.io/v1/convai/conversation/get-signed-url?"
           + urllib.parse.urlencode({"agent_id": agent}))

    def sign() -> str:
        request = urllib.request.Request(url, headers={"xi-api-key": key})
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read())["signed_url"]

    log.info("the public demo is on: %d callers to call as, %.0f minutes a day",
             len(demo.people), demo.left().total_seconds() / 60)
    return demo, sign


def _vapi() -> Callable[[], dict] | None:
    """How a demo call is started on the second voice platform, when it is set up."""
    key, agent = os.environ.get(VAPI_KEY), os.environ.get(VAPI_AGENT)
    log.info("the demo %s", "can also be carried by the second voice platform" if key and agent
             else f"has one voice platform: the second needs {VAPI_KEY} and {VAPI_AGENT}")
    return starter(key, agent) if key and agent else None


def _livekit() -> Callable[[str], dict] | None:
    """How a browser gets into a room of the third way, when a LiveKit project is set."""
    url, key, secret = (os.environ.get(name) for name in
                        (LIVEKIT_URL, LIVEKIT_KEY, LIVEKIT_SECRET))
    return way_in(url, key, secret) if url and key and secret else None


def _voice_program(port: int) -> None:
    """Start the program that listens and speaks on a LiveKit call, beside this one, and
    start it again if it stops. It asks this server what to say, on this machine."""
    def keep_running() -> None:
        soon = 0  # how many times in a row it stopped at once: it is not worth a sixth
        while soon < 5:
            started = time.monotonic()
            code = subprocess.call(
                [sys.executable, "-m", "vetdesk.voice", "start"],
                env={**os.environ, DESK_URL: f"http://127.0.0.1:{port}"})
            soon = soon + 1 if time.monotonic() - started < 60 else 0
            log.warning("the voice program stopped (%s): started again in 5 s", code)
            time.sleep(5)
        log.error("the voice program keeps stopping: calls by LiveKit will not be answered")

    threading.Thread(target=keep_running, daemon=True).start()


def _agenda(kb, clinic):
    """The appointment book: in a file when one is set, so that it outlives a restart,
    and shown in a calendar as well when one is set."""
    path = os.environ.get(AGENDA_FILE)
    agenda = SqliteAgenda(kb, datetime.now, Path(path) if path else None)
    log.info("the agenda is kept in %s", path or "memory: a restart empties it")
    calendar_id, key = os.environ.get(CALENDAR), os.environ.get(CALENDAR_KEY)
    if not (calendar_id and key):
        return agenda

    def who(appointment: Appointment) -> str:
        client = clinic.clients.get(appointment.client_code)
        if client is not None:
            return f"Cliente: {client.raw_name}"
        return f"Sin verificar: {appointment.contact_name}, {appointment.contact_phone}"

    calendar = GoogleCalendar(calendar_id, session_from(key), kb.appointments.slot_minutes)
    mirrored = MirroredAgenda(agenda, calendar, who)
    try:
        taken, missing = mirrored.restore(datetime.now())
        log.info("appointments are shown in a calendar: %d taken from it, %d it was missing",
                 taken, missing)
    except Exception as error:  # a calendar that cannot be read must not stop the phone
        log.warning("the calendar could not be read: %s", error)
    return mirrored


def main() -> None:
    parser = argparse.ArgumentParser(prog="vetdesk.voice.endpoint", description=__doc__)
    parser.add_argument("--data", type=Path, default=Path(os.environ.get("VETDESK_DATA", "data")))
    parser.add_argument("--port", type=int, default=8013)
    parser.add_argument("--host", default="127.0.0.1",
                        help="address to listen on; a tunnel reaches it from outside")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s",
                        datefmt="%H:%M:%S")
    _load_env()
    key = _key(args.host)
    # The clinic's information: a file on the server's disk when one is set, so that it
    # can be changed without a deployment; otherwise the one that comes with the project.
    path = os.environ.get(CLINIC_FILE)
    file = ClinicFile(Path(path)) if path else None
    kb = file.kb if file else load_kb()
    clinic = LegacySqliteSource(args.data / "clinic.db").load()
    llm = create_client()
    agenda = _agenda(kb, clinic)
    # A call from the public demo's page gets an appointment book of its own, in memory,
    # with an appointment already in it for the caller who rings from their own phone.
    def for_the_demo(kb: KnowledgeBase, persona: Persona | None) -> FrontDeskAgent:
        book = SqliteAgenda(kb, datetime.now)
        with_an_appointment(book, persona, clinic, datetime.now())
        return FrontDeskAgent(llm, clinic, kb, book)

    desk = Desk(lambda kb: FrontDeskAgent(llm, clinic, kb, agenda), agenda, kb, file,
                for_the_demo)
    demo, sign = _demo(args.data, clinic)
    if demo is not None:
        demo.booked = lambda persona: slot_ahead(
            SqliteAgenda(desk.kb, datetime.now), datetime.now()) \
            if persona.client_code is not None and persona.pets else None
    switchboard = Switchboard(desk.start_call,
                              stand_ins=stand_ins(os.environ.get(STAND_INS, "")),
                              demo=demo, start_demo_call=desk.start_demo_call)
    token, chat = os.environ.get(TELEGRAM_TOKEN), os.environ.get(TELEGRAM_CHAT)
    reception = Telegram(token, chat) if token and chat else None
    log.info("reception is told what happens %s",
             "on Telegram" if reception else "nowhere: no Telegram chat is set")
    numbers, _ = parse_phones(os.environ.get(TRANSFER_TO, ""))
    log.info("a caller who wants a person %s", "is put through while the clinic is open"
             if numbers else "leaves a message: no number to put calls through to is set")
    path = os.environ.get(CALLS_FILE)
    calls = CallLog(Path(path), keep_days=int(os.environ.get(CALLS_KEEP_DAYS, "90"))) \
        if path else None
    log.info("calls are %s", f"written down in {path}" if calls
             else "not written down: no file is set for them")
    app = build_app(switchboard, key, getattr(llm, "model", ""), desk,
                    os.environ.get(ADMIN_KEY, ""), reception.send if reception else None,
                    numbers[0] if numbers else "", calls, demo, sign, DEMO_PAGE,
                    _vapi() if demo is not None else None, say_and_hang_up,
                    livekit_join := _livekit() if demo is not None else None)
    log.info("the demo %s", "can also be carried by LiveKit: its voice program is started"
             if livekit_join else "is not carried by LiveKit: no project of it is set")
    if livekit_join:
        _voice_program(args.port)
    print(f"The front desk answers at http://{args.host}:{args.port}/v1/chat/completions")
    web.run_app(app, host=args.host, port=args.port, print=None)


if __name__ == "__main__":
    main()
