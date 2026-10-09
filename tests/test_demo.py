"""The public demo: a pass for each call, a budget for the day, and nothing real touched."""

import asyncio
import json
from datetime import datetime, timedelta

import pytest

pytest.importorskip("aiohttp")
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from vetdesk.agent import FrontDeskAgent  # noqa: E402
from vetdesk.kb import load_kb  # noqa: E402
from vetdesk.llm import Reply, ToolCall  # noqa: E402
from vetdesk.llm.scripted import ScriptedClient  # noqa: E402
from vetdesk.scheduling import SqliteAgenda  # noqa: E402
from vetdesk.voice.demo import NO_PASS, TIME_IS_UP, Demo, Full, Persona, personas  # noqa: E402
from vetdesk.voice.endpoint import Switchboard, build_app  # noqa: E402

NOW = datetime(2026, 11, 3, 10, 15)  # a Tuesday morning
SLOT = "2026-11-09T16:30"
KEY = "the-platforms-key"
HANG_UP = [{"type": "function", "function": {"name": "end_call", "description": "End.",
                                             "parameters": {"type": "object"}}}]
MARTA = Persona("own", "Marta Soler Vidal", "Port Blau", ("Toby",), "+34600111222", "es")
NOBODY = Persona("hidden", "Pau Riera Font", "Vallserena", ("Nit",), None, "ca")


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


# --- who a visitor can call as --------------------------------------------------------------

def test_a_visitor_calls_as_one_of_the_clinics_own_test_callers(scenarios, clinic):
    people = {persona.key: persona for persona in personas(scenarios, clinic)}
    assert list(people) == ["own", "hidden", "borrowed", "stranger"]
    assert people["own"].caller_number is not None and people["own"].pets
    assert people["hidden"].caller_number is None and people["stranger"].caller_number is None
    # From somebody else's phone: a number that is on file, and not for them.
    assert people["borrowed"].caller_number not in (None, people["own"].caller_number)
    on_file = [c.raw_name for c in clinic.clients_by_phone(people["borrowed"].caller_number)]
    assert on_file and people["borrowed"].name.split()[0] not in " ".join(on_file)


def test_each_caller_is_taken_the_way_the_page_tells_the_visitor(scenarios, clinic):
    """The first caller of the usual kind has their name mistyped on file ("Deigo"). Offered
    to a visitor as "your name should be enough" while the resolver did not forgive it,
    they were asked to spell it twice. Whoever the page offers is asked of the resolver
    first, whatever its rules are that day."""
    from vetdesk.identity.resolver import Evidence, IdentityResolver

    resolver = IdentityResolver(clinic)
    people = {persona.key: persona for persona in personas(scenarios, clinic)}

    def level(persona, everything):
        more = {"pet_name": persona.pets[0], "town": persona.town} if everything else {}
        return resolver.resolve(Evidence(persona.caller_number, persona.name, **more)).level

    assert level(people["own"], False) == "confirmed"
    assert level(people["hidden"], False) != "confirmed"
    assert level(people["hidden"], True) == "confirmed"
    assert level(people["borrowed"], True) != "confirmed"
    assert level(people["stranger"], True) != "confirmed"


# --- the day's minutes ----------------------------------------------------------------------

def test_a_day_holds_so_many_minutes_and_a_call_counts_for_what_it_took():
    clock = Clock()
    demo = Demo([MARTA], clock, minutes_a_day=9, minutes_a_call=3, calls_an_address=10)
    first, second = demo.start("own", "a"), demo.start("own", "b")
    assert first != second
    # A call that may still be going on counts whole.
    assert demo.left() == timedelta(minutes=3)
    demo.start("own", "c")
    with pytest.raises(Full) as full:
        demo.start("own", "d")
    assert full.value.why == "day"

    # Three minutes on: the first was heard of for forty seconds, the second never made,
    # the third went on to the end.
    clock.now += timedelta(seconds=40)
    assert demo.call(first).persona is MARTA
    clock.now += timedelta(seconds=139)
    demo.call(next(token for token in demo._passes if token not in (first, second)))
    clock.now += timedelta(seconds=1)
    assert demo.over(first)
    # One minute (forty seconds and the last answer), nothing, and three whole.
    assert demo.left() == timedelta(minutes=9) - timedelta(seconds=60) - timedelta(minutes=3)
    demo.start("own", "d")

    clock.now += timedelta(days=1)  # another day: the count starts again
    assert demo.left() == timedelta(minutes=9) and demo.call(first) is None


def test_one_address_gets_so_many_calls_a_day_and_nobody_calls_as_a_stranger_to_the_list():
    demo = Demo([MARTA], Clock(), calls_an_address=2)
    demo.start("own", "1.2.3.4")
    demo.start("own", "1.2.3.4")
    with pytest.raises(Full) as full:
        demo.start("own", "1.2.3.4")
    assert full.value.why == "address"
    demo.start("own", "5.6.7.8")
    with pytest.raises(KeyError):
        demo.start("the vet", "5.6.7.8")
    assert demo.call("made-up") is None and demo.over("made-up")


# --- through the server ---------------------------------------------------------------------

def _post(app, *requests):
    """Requests one after another: (path, body, key). Status and text of each answer.
    Something to call, in between, is called: time passing."""
    async def run():
        async with TestClient(TestServer(app)) as client:
            answers = []
            for request in requests:
                if callable(request):
                    request()
                    continue
                if isinstance(request, str):  # something to read, not to send
                    await asyncio.sleep(0.05)
                    response = await client.get(request)
                    answers.append((response.status, await response.text()))
                    continue
                path, body, key = request
                response = await client.post(
                    path, json=body, headers={"Authorization": f"Bearer {key}"} if key else {})
                answers.append((response.status, await response.text()))
            await asyncio.sleep(0.05)
            people = await client.get("/demo/people")
            return answers, (people.status, await people.text())

    return asyncio.run(run())


def _chat(token, *said):
    system = f"vetdesk-conversation: demo-{token}\nvetdesk-demo: {token}"
    messages = [{"role": "system", "content": system}]
    for index, text in enumerate(said):
        if index:
            messages.append({"role": "assistant", "content": "(heard)"})
        messages.append({"role": "user", "content": text})
    return ("/v1/chat/completions",
            {"model": "x", "stream": True, "messages": messages, "tools": HANG_UP}, KEY)


def _spoken(stream):
    chunks = [json.loads(line[6:]) for line in stream.splitlines()
              if line.startswith("data: {")]
    deltas = [chunk["choices"][0]["delta"] for chunk in chunks]
    return ("".join(delta.get("content") or "" for delta in deltas),
            [call["function"]["name"] for delta in deltas for call in delta.get("tool_calls", [])])


def _front_desk(clinic, steps, demo, sign=lambda: "wss://voice.example/one-call", told=None,
                vapi_call=None, vapi_say=None):
    kb = load_kb()
    real = SqliteAgenda(kb, lambda: NOW)
    model = ScriptedClient(list(steps))
    numbers = []

    def start_demo_call(persona):
        number = persona.caller_number if persona else None
        numbers.append(number)
        return FrontDeskAgent(model, clinic, kb, SqliteAgenda(kb, lambda: NOW),
                              lambda: NOW).start_call(number)

    switchboard = Switchboard(
        FrontDeskAgent(model, clinic, kb, real, lambda: NOW).start_call, demo=demo,
        start_demo_call=start_demo_call)
    app = build_app(switchboard, KEY, demo=demo, sign=sign,
                    tell=told.append if told is not None else None, demo_page="<p>demo</p>",
                    vapi_call=vapi_call, vapi_say=vapi_say)
    return app, real, numbers


def test_the_page_asks_for_a_call_and_gets_a_pass_and_where_to_call(clinic):
    demo = Demo([MARTA, NOBODY], Clock(), minutes_a_day=6, minutes_a_call=3)
    app, _, _ = _front_desk(clinic, [], demo)
    (given, unknown, nothing, second, full), people = _post(
        app, ("/demo/call", {"as": "own"}, None), ("/demo/call", {"as": "the vet"}, None),
        ("/demo/call", {}, None), ("/demo/call", {"as": "hidden"}, None),
        ("/demo/call", {"as": "own"}, None))
    answer = json.loads(given[1])
    assert given[0] == 200 and answer["signed_url"] == "wss://voice.example/one-call"
    assert answer["seconds"] == 180 and demo.call(answer["pass"]).persona is MARTA
    assert unknown[0] == 404 and nothing[0] == 400 and second[0] == 200
    assert full[0] == 429 and json.loads(full[1])["why"] == "day"
    shown = json.loads(people[1])
    assert people[0] == 200 and shown["minutes_left"] == 0 and shown["seconds_a_call"] == 180
    assert shown["people"][0] == {"key": "own", "name": "Marta Soler Vidal", "town": "Port Blau",
                                  "pets": ["Toby"], "phone": "600 111 222", "language": "es",
                                  "appointment": None}
    assert shown["people"][1]["phone"] is None


def test_when_the_voice_platform_gives_no_address_the_minutes_are_given_back(clinic):
    def sign():
        raise OSError("no")

    demo = Demo([MARTA], Clock(), minutes_a_day=3, minutes_a_call=3)
    app, _, _ = _front_desk(clinic, [], demo, sign)
    (refused,), _ = _post(app, ("/demo/call", {"as": "own"}, None))
    assert refused[0] == 503 and demo.left() == timedelta(minutes=3)


def test_a_demo_call_is_taken_as_from_that_callers_phone_and_touches_nothing_real(clinic):
    """Its appointment goes in a book of its own, and reception hears nothing of it."""
    book = ToolCall("b", "book_appointment", {
        "start": SLOT, "reason": "revisión", "pet_name": "Toby",
        "contact_name": "Marta Soler", "contact_phone": "600 11 22 33"})
    look = ToolCall("a", "get_availability", {
        "date_from": SLOT[:10], "date_to": SLOT[:10], "part_of_day": "afternoon"})
    demo, told = Demo([MARTA], Clock()), []
    app, real, numbers = _front_desk(
        clinic, [Reply("", (look,), "tool_calls"), Reply("¿A las cuatro y media?"),
                 Reply("", (book,), "tool_calls"), Reply("Reservado.")], demo, told=told)
    token = demo.start("own", "a")
    wants = "Una revisión para mi perro Toby, el lunes."
    (_, answer, after, nothing), _ = _post(
        app, _chat(token, wants), _chat(token, wants, "Sí, a esa hora."),
        f"/demo/result?pass={token}", "/demo/result?pass=made-up")
    assert answer[0] == 200 and _spoken(answer[1]) == ("Reservado.", [])
    assert numbers == ["+34600111222"]
    assert real.all() == [] and told == []
    # What the visitor could not hear on the call, the page is told when it is over: what
    # the agent took them for, and what reception would have been sent.
    shown = json.loads(after[1])
    assert after[0] == 200 and shown["identity"] == {"level": "none", "why": "not_asked"}
    (notice,) = shown["reception"]
    assert notice.startswith("CITA NUEVA") and "SIN VERIFICAR: dice ser Marta Soler" in notice
    assert nothing[0] == 404


def test_a_call_with_no_pass_is_told_so_and_closed_without_asking_any_model(clinic):
    demo = Demo([MARTA], Clock())
    app, _, numbers = _front_desk(clinic, [], demo)  # a model asked would have no answer
    (made_up, empty), _ = _post(app, _chat("made-up", "Hola."), _chat("", "Hola."))
    assert _spoken(made_up[1]) == (NO_PASS, ["end_call"])
    assert _spoken(empty[1]) == (NO_PASS, ["end_call"])
    assert numbers == [None, None]


def test_a_demo_call_that_has_run_its_time_is_said_goodbye_to_and_closed(clinic):
    clock = Clock()
    demo = Demo([MARTA], clock, minutes_a_call=3)
    app, _, _ = _front_desk(clinic, [Reply("Dígame.")], demo)
    token = demo.start("own", "a")
    def three_minutes_on():
        clock.now += timedelta(minutes=3)

    (first, late), _ = _post(app, _chat(token, "Hola, buenos días."), three_minutes_on,
                             _chat(token, "Hola, buenos días.", "Quería una cita."))
    assert _spoken(first[1]) == ("Dígame.", [])
    assert _spoken(late[1]) == (TIME_IS_UP["es"], ["end_call"])


# --- the same demo, carried by a second voice platform --------------------------------------

VAPI_HANG_UP = [{"type": "function", "function": {"name": "endCall"}}]


def _vapi_chat(call, *said):
    """A request as Vapi sends it: the conversation, and the call it is for by its id."""
    from vetdesk.voice.endpoint import platform_key

    messages = [{"role": "assistant", "content": "Clínica veterinaria Planeta Animal."}]
    for index, text in enumerate(said):
        if index:
            messages.append({"role": "assistant", "content": "(heard)"})
        messages.append({"role": "user", "content": text})
    return ("/vapi/chat/completions",
            {"model": "vetdesk", "stream": True, "messages": messages, "tools": VAPI_HANG_UP,
             "call": {"id": call, "type": "webCall",
                      "monitor": {"controlUrl": f"https://steer.vapi.ai/{call}/control"}}},
            platform_key(KEY, "vapi"))


def test_a_call_by_the_second_platform_is_started_by_our_server_with_the_pass(clinic):
    """The platform starts a browser call for whoever holds a key meant to sit in the
    page. The page is given none: its library asks our server, with the pass, and our
    server starts the call and remembers which pass the call it was given is for."""
    started = []

    def vapi_call():
        started.append(f"call-{len(started) + 1}")
        return {"id": started[-1], "webCallUrl": "https://rooms.example/one",
                "transport": {"provider": "daily"}, "orgId": "the-account",
                "monitor": {"controlUrl": "https://steer.example/one"}}

    demo = Demo([MARTA, NOBODY], Clock(), minutes_a_day=9, minutes_a_call=3)
    app, real, numbers = _front_desk(clinic, [Reply("Dígame, Marta.")], demo,
                                     vapi_call=vapi_call)
    token = demo.start("own", "a")
    (asked, other, no_pass, made_up, call, again, answer, stray), people = _post(
        app, ("/demo/call", {"as": "hidden", "platform": "vapi"}, None),
        ("/demo/call", {"as": "own", "platform": "another"}, None),
        ("/demo/vapi/call/web", {"assistantId": "demo"}, None),
        ("/demo/vapi/call/web", {"assistantId": "demo"}, "made-up"),
        ("/demo/vapi/call/web", {"assistantId": "demo"}, token),
        ("/demo/vapi/call/web", {"assistantId": "demo"}, token),  # a pass starts one call
        _vapi_chat("call-1", "Hola, soy Marta Soler."),
        _vapi_chat("call-9", "Hola, soy Marta Soler."))  # a call our server did not start
    # The page is given a pass and no address: the call is not started until the
    # platform's library asks for it.
    given = json.loads(asked[1])
    assert asked[0] == 200 and set(given) == {"pass", "platform", "seconds"}
    assert demo.call(given["pass"]).persona is NOBODY
    assert other[0] == 404 and no_pass[0] == 401 and made_up[0] == 401
    assert json.loads(people[1])["platforms"] == ["elevenlabs", "vapi"]
    # The page is handed what its library needs to join, and nothing else of the call.
    assert call[0] == 201 and json.loads(call[1]) == {
        "id": "call-1", "webCallUrl": "https://rooms.example/one",
        "transport": {"provider": "daily"}}
    assert again[0] == 401 and started == ["call-1"]
    # The call is taken as from the phone of whoever the visitor chose to call as.
    assert _spoken(answer[1]) == ("Dígame, Marta.", []) and numbers[0] == "+34600111222"
    assert _spoken(stray[1]) == (NO_PASS, ["endCall"]) and numbers[1:] == [None]
    assert real.all() == []


def test_the_second_platform_is_told_to_hang_up_by_its_own_tools_name(clinic):
    clock = Clock()
    demo = Demo([MARTA], clock, minutes_a_call=3)
    app, _, _ = _front_desk(
        clinic, [Reply("Abrimos a las nueve y media."), Reply("De nada. ¡Que vaya muy bien!")],
        demo, vapi_call=lambda: {"id": "call-1", "webCallUrl": "https://rooms.example/one"})
    token = demo.start("own", "a")

    def three_minutes_on():
        clock.now += timedelta(minutes=3)

    (_, first, bye, late), _ = _post(
        app, ("/demo/vapi/call/web", {}, token), _vapi_chat("call-1", "¿A qué hora abrís?"),
        _vapi_chat("call-1", "¿A qué hora abrís?", "No, eso es todo. Gracias."),
        three_minutes_on,
        _vapi_chat("call-1", "¿A qué hora abrís?", "No, eso es todo. Gracias.", "¿Oiga?"))
    assert _spoken(first[1]) == ("Abrimos a las nueve y media.", [])
    assert _spoken(bye[1]) == ("De nada. ¡Que vaya muy bien!", ["endCall"])
    assert _spoken(late[1]) == (TIME_IS_UP["es"], ["endCall"])


def test_the_second_platform_says_the_farewell_itself_and_then_hangs_up(clinic):
    """Heard on its first calls: its tool for hanging up ends the call at once, over the
    farewell handed to it ahead of the tool. The call is told to say it and end after."""
    from vetdesk.voice.vapi import say_and_hang_up

    told, takes_it = [], [True]

    def vapi_say(control, text):
        told.append((control, text))
        return takes_it[0]

    def front_desk(steps):
        demo = Demo([MARTA], Clock())
        app, _, _ = _front_desk(
            clinic, steps, demo, vapi_say=vapi_say,
            vapi_call=lambda: {"id": "call-1", "webCallUrl": "https://rooms.example/one"})
        return app, demo.start("own", "a")

    bye = [Reply("Abrimos a las nueve y media."), Reply("De nada. ¡Que vaya muy bien!")]
    said = ("¿A qué hora abrís?", "No, eso es todo. Gracias.")
    app, token = front_desk(bye)
    (_, first, last, again), _ = _post(
        app, ("/demo/vapi/call/web", {}, token), _vapi_chat("call-1", said[0]),
        _vapi_chat("call-1", *said), _vapi_chat("call-1", *said))
    assert _spoken(first[1]) == ("Abrimos a las nueve y media.", [])
    # Nothing to say and no tool in our answer: the words went to the call itself, once,
    # however often the platform asks for that answer.
    assert _spoken(last[1]) == ("", []) and _spoken(again[1]) == ("", [])
    assert told == [("https://steer.vapi.ai/call-1/control", "De nada. ¡Que vaya muy bien!")]
    # A call that will not take the order is closed as before: the words, then the tool.
    takes_it[0] = False
    app, token = front_desk(bye)
    (_, _, last), _ = _post(app, ("/demo/vapi/call/web", {}, token),
                            _vapi_chat("call-1", said[0]), _vapi_chat("call-1", *said))
    assert _spoken(last[1]) == ("De nada. ¡Que vaya muy bien!", ["endCall"])
    # Nothing is sent to an address that is not the platform's own.
    assert say_and_hang_up("https://steer.example/call-1/control", "Adiós.") is False
    assert say_and_hang_up("http://steer.vapi.ai/call-1/control", "Adiós.") is False


def test_when_the_second_platform_starts_no_call_the_minutes_are_given_back(clinic):
    def vapi_call():
        raise OSError("no")

    demo = Demo([MARTA], Clock(), minutes_a_day=3, minutes_a_call=3)
    app, _, _ = _front_desk(clinic, [], demo, vapi_call=vapi_call)
    token = demo.start("own", "a")
    (refused,), _ = _post(app, ("/demo/vapi/call/web", {}, token))
    assert refused[0] == 503 and demo.left() == timedelta(minutes=3)
    # With no second platform set up, the page is offered one and the route is not there.
    off, _, _ = _front_desk(clinic, [], Demo([MARTA], Clock()))
    (asked, call), people = _post(off, ("/demo/call", {"as": "own", "platform": "vapi"}, None),
                                  ("/demo/vapi/call/web", {}, "a-pass"))
    assert asked[0] == 404 and call[0] == 404
    assert json.loads(people[1])["platforms"] == ["elevenlabs"]


def test_a_permit_to_start_a_call_is_good_for_a_minute_and_for_our_assistant_only():
    """Vapi starts no browser call with the account's private key, and the key that does
    is meant for a page. What our server uses instead is a token signed with the private
    key, which never leaves it."""
    import base64
    import hashlib
    import hmac

    from vetdesk.voice.vapi import permit, starter

    token = permit("the-private-key", "the-account", "our-assistant", now=lambda: 1000.0)
    head, claims, mark = token.split(".")

    def read(part):
        return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))

    assert read(head) == {"alg": "HS256", "typ": "JWT"}
    assert read(claims) == {
        "orgId": "the-account", "iat": 1000, "exp": 1060,
        "token": {"tag": "public", "restrictions": {
            "enabled": True, "allowedAssistantIds": ["our-assistant"],
            "allowTransientAssistant": False}}}
    signed = hmac.new(b"the-private-key", f"{head}.{claims}".encode(), hashlib.sha256).digest()
    assert base64.urlsafe_b64decode(mark + "=" * (-len(mark) % 4)) == signed
    assert "the-private-key" not in token

    asked = []

    def ask(method, path, key, body=None, timeout=30):
        asked.append((method, path, key, body))
        return {"orgId": "the-account"} if method == "GET" else {"id": "call-1"}

    start = starter("the-private-key", "our-assistant", ask)
    assert start() == {"id": "call-1"} and start() == {"id": "call-1"}
    # Whose the assistant is, asked once and with the private key; each call, with a
    # permit and never with that key.
    assert [(method, path) for method, path, _, _ in asked] == [
        ("GET", "/assistant/our-assistant"), ("POST", "/call/web"), ("POST", "/call/web")]
    assert asked[0][2] == "the-private-key" and asked[1][2].count(".") == 2
    assert asked[1][3] == {"assistantId": "our-assistant"}


# --- the demo's own agent on the voice platform ---------------------------------------------

def test_the_demos_agent_needs_a_pass_has_limits_and_nobody_to_put_a_call_through_to(monkeypatch):
    from vetdesk.voice.elevenlabs_agent import config, demo_settings

    monkeypatch.setenv("VETDESK_TTS_VOICE", "voice")
    monkeypatch.setenv("VETDESK_TRANSFER_TO", "+34600000000")
    phone = config("https://example.test", "secret")
    assert "transfer_to_number" in phone["conversation_config"]["agent"]["prompt"]["built_in_tools"]
    demo = demo_settings(config("https://example.test", "secret"))
    assert demo["name"] == "Planeta Animal demo (vetdesk)" != phone["name"]
    agent = demo["conversation_config"]["agent"]
    assert "vetdesk-demo: {{demo_pass}}" in agent["prompt"]["prompt"]
    assert "vetdesk-caller" not in agent["prompt"]["prompt"]
    assert list(agent["prompt"]["built_in_tools"]) == ["language_detection", "end_call"]
    assert agent["dynamic_variables"] == {"dynamic_variable_placeholders": {"demo_pass": ""}}
    # The same voice, ears and manners as on the phone.
    for part in ("tts", "turn", "vad", "language_presets"):
        assert demo["conversation_config"][part] == phone["conversation_config"][part]
    assert demo["conversation_config"]["conversation"] == {"max_duration_seconds": 200}
    platform = demo["platform_settings"]
    assert platform["auth"] == {"enable_auth": True}
    assert platform["call_limits"] == {"agent_concurrency_limit": 2, "daily_limit": 60,
                                       "bursting_enabled": False}
    assert platform["privacy"] == {"record_voice": False, "retention_days": 30}


def test_the_page_is_served_only_when_the_demo_is_set_up(clinic):
    from vetdesk.voice.demo_page import PAGE, SDK, VAPI_SDK

    assert "@elevenlabs/client@1." in SDK and SDK in PAGE  # a version somebody saw work
    assert "@vapi-ai/web@2." in VAPI_SDK and VAPI_SDK in PAGE
    assert 'fetch("demo/call"' in PAGE and "demo_pass" in PAGE
    # The second platform's library asks this server to start its call: no address of the
    # platform's is in the page for it, and no key.
    assert 'new URL("demo/vapi", location.href)' in PAGE and "api.vapi.ai" not in PAGE
    # The choice of platform is for whoever opens the page asking for it.
    assert 'new URLSearchParams(location.search).get("via")' in PAGE
    assert "(via !== null && info && info.platforms) || []" in PAGE
    # The page names nobody: it is served from an address that is not the author's own.
    assert "Barceló" not in PAGE and "portfolio" not in PAGE.lower()
    assert "ayuda de IA" not in PAGE and "help of AI" not in PAGE  # nor how it was made

    async def get(app, *paths):
        async with TestClient(TestServer(app)) as client:
            answers = []
            for path in paths:
                response = await client.get(path)
                answers.append((response.status, await response.text()))
            return answers

    demo = Demo([MARTA], Clock())
    on, _, _ = _front_desk(clinic, [], demo)
    assert asyncio.run(get(on, "/demo")) == [(200, "<p>demo</p>")]
    off = build_app(Switchboard(lambda number: None), KEY, demo_page="<p>demo</p>")
    assert [status for status, _ in asyncio.run(get(off, "/demo", "/demo/people"))] == [404, 404]


def test_the_caller_on_their_own_phone_starts_with_an_appointment_to_move_or_cancel(
        scenarios, clinic):
    """What the 2025 pilot could not do, and what nobody would see in a call of three
    minutes that starts with an empty book."""
    from vetdesk.voice.demo import BOOKED_FOR, slot_ahead, with_an_appointment

    kb = load_kb()
    people = {persona.key: persona for persona in personas(scenarios, clinic)}
    own = people["own"]
    assert own.client_code is not None
    assert all(people[key].client_code is None for key in ("hidden", "borrowed", "stranger"))

    book = SqliteAgenda(kb, lambda: NOW)
    told = slot_ahead(SqliteAgenda(kb, lambda: NOW), NOW)  # what the page says, beforehand
    made = with_an_appointment(book, own, clinic, NOW)
    assert made.start == told and told.date() >= (NOW + timedelta(days=2)).date()
    assert told.hour < 14 and (made.reason, made.pet_name) == (BOOKED_FOR, own.pets[0])
    assert made.verified and [a.appointment_id for a in book.for_client(own.client_code)] == [
        made.appointment_id]
    # Nobody else starts with one, and a call with no pass starts with nothing.
    for other in (people["hidden"], people["borrowed"], None):
        assert with_an_appointment(SqliteAgenda(kb, lambda: NOW), other, clinic, NOW) is None


def test_the_page_is_told_when_that_appointment_is(clinic):
    start = datetime(2026, 11, 5, 9, 30)
    demo = Demo([MARTA, NOBODY], Clock(),
                booked=lambda persona: start if persona is MARTA else None)
    app, _, _ = _front_desk(clinic, [], demo)
    _, people = _post(app)
    shown = json.loads(people[1])["people"]
    assert shown[0]["appointment"] == {
        "pet": "Toby", "es": "jueves 5 de noviembre a las nueve y media de la mañana",
        "en": "Thursday 5 November at nine thirty in the morning"}
    assert shown[1]["appointment"] is None


def test_what_the_agent_took_a_demo_caller_for_is_put_in_words_the_page_can_show(
        scenarios, clinic):
    """Each caller the page offers, taken through the resolver as the call would."""
    from vetdesk.agent import Toolbox
    from vetdesk.voice.bridge import Line
    from vetdesk.voice.endpoint import what_happened

    kb = load_kb()
    people = {persona.key: persona for persona in personas(scenarios, clinic)}

    def after_saying(persona, everything=True):
        toolbox = Toolbox(clinic, kb, SqliteAgenda(kb, lambda: NOW), lambda: NOW,
                          persona.caller_number)
        said = {"name": persona.name}
        if everything:
            said |= {"pet_name": persona.pets[0], "town": persona.town}
        toolbox.heard(" ".join(said.values()))
        toolbox.run(ToolCall("i", "identify_client", {
            "name": None, "pet_name": None, "town": None, "name_spelled": False, **said}))
        call = type("Call", (), {"session": toolbox.session})()
        line = Line.__new__(Line)
        line.call, line.told = call, []
        return what_happened(line)["identity"]

    own = after_saying(people["own"], everything=False)
    assert (own["level"], own["by"]) == ("confirmed", "phone")
    assert own["mistyped"] == ("Deigo" in own["name"])  # said only when it is so
    hidden = after_saying(people["hidden"])
    assert (hidden["level"], hidden["by"], hidden["mistyped"]) == ("confirmed", "pet_town", False)
    assert after_saying(people["borrowed"]) == {"level": "none", "why": "other_phone"}
    assert after_saying(people["stranger"]) == {"level": "none", "why": "not_a_client"}
    assert after_saying(people["hidden"], everything=False) == {"level": "none",
                                                               "why": "not_enough"}
