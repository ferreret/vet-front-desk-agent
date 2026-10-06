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
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from aiohttp import web

from ..agent import FrontDeskAgent
from ..agent.agent import Call, Turn
from ..agent.prompt import THROUGH
from ..evals.cost import cost
from ..kb import KnowledgeBase, load_kb, why_not
from ..language import SPOKEN
from ..legacy import LegacySqliteSource
from ..legacy.normalize import parse_phones
from ..llm import create_client
from ..notices import Notice
from ..scheduling import Appointment, SqliteAgenda
from ..scheduling.google_calendar import CALENDAR, GoogleCalendar, MirroredAgenda, session_from
from ..scheduling.google_calendar import KEY as CALENDAR_KEY
from .bridge import Line, is_goodbye, may_end
from .clinic_file import PAGE, ClinicFile
from .telegram import CHAT as TELEGRAM_CHAT
from .telegram import TOKEN as TELEGRAM_TOKEN
from .telegram import Telegram

log = logging.getLogger("vetdesk.endpoint")

KEY_NAME = "VETDESK_ENDPOINT_KEY"
STAND_INS = "VETDESK_CALLER_STANDS_IN_FOR"
AGENDA_FILE = "VETDESK_AGENDA"
CLINIC_FILE, ADMIN_KEY = "VETDESK_CLINIC", "VETDESK_ADMIN_KEY"
IDLE_SECONDS = 30 * 60  # a call nobody has asked about for this long is over
# ElevenLabs wraps the agent's prompt in text of its own, so the two markers are looked for
# anywhere in it, under names nothing else would use.
_CONVERSATION = re.compile(r"vetdesk-conversation:\s*(\S+)")
_CALLER = re.compile(r"vetdesk-caller:[ \t]*([+\d][\d ()-]*)?")
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


class Switchboard:
    """Which call each request belongs to. One `Line` per conversation."""

    def __init__(self, start_call: Callable[[str | None], Call],
                 clock: Callable[[], float] = time.monotonic,
                 stand_ins: dict[str, str] | None = None) -> None:
        self._start_call, self._clock = start_call, clock
        self._lines: dict[str, tuple[Line, float]] = {}
        # Real numbers that call as a number of the made-up clinic: see `stand_ins`.
        self._stand_ins = stand_ins or {}

    def line(self, messages: list[dict], can_transfer: bool = False) -> Line:
        system = " \n".join(_text(m.get("content")) for m in messages if m.get("role") == "system")
        found = _CONVERSATION.search(system)
        if found and "{{" not in found.group(1):
            conversation = found.group(1)
        else:
            # No id from the platform: the opening of a conversation never changes, so it
            # serves as one. Two calls that open with the same words would be confused,
            # which is why the two prompt lines above matter.
            opening = json.dumps(messages[:2], sort_keys=True, ensure_ascii=False)
            conversation = "opening-" + hashlib.sha256(opening.encode()).hexdigest()[:16]
        now = self._clock()
        self._lines = {k: v for k, v in self._lines.items() if now - v[1] < IDLE_SECONDS}
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
            line.listening_in = FIRST_LANGUAGE
            self._lines[conversation] = (line, now)
        line, _ = self._lines[conversation]
        self._lines[conversation] = (line, now)
        return line


class Desk:
    """The front desk as it is now: made again whenever the clinic's information changes.

    Calls already going on keep the information they started with; the next call gets the
    new one, and the agenda its new opening hours.
    """

    def __init__(self, make: Callable[[KnowledgeBase], FrontDeskAgent], agenda,
                 kb: KnowledgeBase, file: ClinicFile | None = None) -> None:
        self._make, self._agenda, self.file = make, agenda, file
        self._agent = make(kb)

    def start_call(self, number: str | None, can_transfer: bool = False) -> Call:
        return self._agent.start_call(number, can_transfer)

    def replace(self, text: str) -> None:
        kb = self.file.replace(text)  # raises, and changes nothing, if the text is wrong
        self._agent = self._make(kb)
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


def build_app(switchboard: Switchboard, key: str, model: str = "", desk: Desk | None = None,
              admin_key: str = "",
              tell: Callable[[Notice], None] | None = None,
              transfer_to: str = "") -> web.Application:
    """`model` is the agent's own model, named only to put a price on each answer. With a
    `desk` whose information is in a file and an `admin_key`, that information can be read
    and replaced through the server. `tell` is how reception is told what happens on a
    call: see `notices`. `transfer_to` is the number a person answers at, to put calls
    through to."""
    agent_model = model
    put_through: set[int] = set()  # the lines the platform has been told to put through
    waiting: set[asyncio.Future] = set()  # answers being worked out for a request to come

    def spent(turn: Turn) -> None:
        usage, price = turn.usage, cost(agent_model, turn.usage)
        log.info("model: %d requests, %d tokens in (%d from cache), %d out%s", turn.requests,
                 usage.input_tokens + usage.cache_read_tokens + usage.cache_write_tokens,
                 usage.cache_read_tokens, usage.output_tokens,
                 f", ${price:.4f}" if price is not None else "")

    def finished(line: Line) -> Callable[[Turn], None]:
        """What to do when a turn is over, whether or not anybody is still listening."""
        def after(turn: Turn) -> None:
            spent(turn)
            waiting_for_reception = line.call.session.notices
            while waiting_for_reception:
                notice = waiting_for_reception.pop(0)
                log.info("for reception: %s", notice.kind)
                if tell:
                    tell(notice)
        return after

    async def use(response: web.StreamResponse, request_id: str, model: str, tool: str,
                  **arguments: str) -> web.StreamResponse:
        """Answer with a call to one of the platform's own tools, and nothing else."""
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
        said = [_text(m.get("content")) for m in messages if m.get("role") == "user"]
        request_id = "chatcmpl-" + secrets.token_hex(8)
        model = str(body.get("model", "vetdesk"))

        response = web.StreamResponse(headers={"Content-Type": "text/event-stream",
                                               "Cache-Control": "no-cache"})
        await response.prepare(request)
        started = time.perf_counter()
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
                waiting.add(task := asyncio.ensure_future(
                    _run(line.answer(said[-1], on_turn=finished(line), turn=len(said)))))
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
                log.info("caller: %s", heard)
                # A call may be over after this line (nothing said, or a goodbye). Then
                # the answer is not said piece by piece but kept whole: if it is a
                # goodbye too, the platform is handed it to say and told to hang up.
                closing = _offers(body, END_TOOL) and not after_a_tool and may_end(said[-1])
                session = line.call.session
                answer, first = [], None
                async for piece in line.answer(heard, on_turn=finished(line), turn=turn,
                                               note=note):
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
                if session.transfer and transfer_to and id(line) not in put_through:
                    put_through.add(id(line))
                    log.info("the caller wants a person: the platform is told to put the "
                             "call through")
                    return await use(response, request_id, model, TRANSFER_TOOL,
                                     reason="the caller asked to speak to a person",
                                     transfer_number=transfer_to,
                                     client_message=THROUGH[session.language],
                                     agent_message=session.transfer)
                if closing and is_goodbye(text):
                    log.info("the goodbyes are said: the platform is told to hang up")
                    return await use(response, request_id, model, END_TOOL,
                                     reason="the caller and the agent have said goodbye",
                                     message=text)
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

    app = web.Application()
    app.router.add_post("/v1/chat/completions", chat_completions)
    app.router.add_post("/chat/completions", chat_completions)
    app.router.add_get("/health", health)
    if desk is not None and desk.file is not None and admin_key:
        app.router.add_get("/clinic", clinic)
        app.router.add_put("/clinic", clinic)
        app.router.add_get("/clinic/edit", edit)
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
    desk = Desk(lambda kb: FrontDeskAgent(llm, clinic, kb, agenda), agenda, kb, file)
    switchboard = Switchboard(desk.start_call,
                              stand_ins=stand_ins(os.environ.get(STAND_INS, "")))
    token, chat = os.environ.get(TELEGRAM_TOKEN), os.environ.get(TELEGRAM_CHAT)
    reception = Telegram(token, chat) if token and chat else None
    log.info("reception is told what happens %s",
             "on Telegram" if reception else "nowhere: no Telegram chat is set")
    numbers, _ = parse_phones(os.environ.get(TRANSFER_TO, ""))
    log.info("a caller who wants a person %s", "is put through while the clinic is open"
             if numbers else "leaves a message: no number to put calls through to is set")
    app = build_app(switchboard, key, getattr(llm, "model", ""), desk,
                    os.environ.get(ADMIN_KEY, ""), reception.send if reception else None,
                    numbers[0] if numbers else "")
    print(f"The front desk answers at http://{args.host}:{args.port}/v1/chat/completions")
    web.run_app(app, host=args.host, port=args.port, print=None)


if __name__ == "__main__":
    main()
