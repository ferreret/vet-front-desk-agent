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
    """The first caller of the usual kind has their name misspelt on file ("Deigo") and is
    identified by nobody: offered to a visitor as "your name should be enough", they were
    asked to spell it twice. Whoever the page offers is asked of the resolver first."""
    from vetdesk.identity.resolver import Evidence, IdentityResolver

    resolver = IdentityResolver(clinic)
    people = {persona.key: persona for persona in personas(scenarios, clinic)}

    def level(persona, everything):
        more = {"pet_name": persona.pets[0], "town": persona.town} if everything else {}
        return resolver.resolve(Evidence(persona.caller_number, persona.name, **more)).level

    assert people["own"].name != "Diego Esteve Planas"  # the misspelt one is passed over
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


def _front_desk(clinic, steps, demo, sign=lambda: "wss://voice.example/one-call", told=None):
    kb = load_kb()
    real = SqliteAgenda(kb, lambda: NOW)
    model = ScriptedClient(list(steps))
    numbers = []

    def start_demo_call(number):
        numbers.append(number)
        return FrontDeskAgent(model, clinic, kb, SqliteAgenda(kb, lambda: NOW),
                              lambda: NOW).start_call(number)

    switchboard = Switchboard(
        FrontDeskAgent(model, clinic, kb, real, lambda: NOW).start_call, demo=demo,
        start_demo_call=start_demo_call)
    app = build_app(switchboard, KEY, demo=demo, sign=sign,
                    tell=told.append if told is not None else None, demo_page="<p>demo</p>")
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
                                  "pets": ["Toby"], "phone": "600 111 222", "language": "es"}
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
    demo, told = Demo([MARTA], Clock()), []
    app, real, numbers = _front_desk(
        clinic, [Reply("", (book,), "tool_calls"), Reply("Reservado.")], demo, told=told)
    token = demo.start("own", "a")
    (answer,), _ = _post(app, _chat(token, "Una revisión para mi perro Toby, el lunes."))
    assert answer[0] == 200 and _spoken(answer[1]) == ("Reservado.", [])
    assert numbers == ["+34600111222"]
    assert real.all() == [] and told == []


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
    from vetdesk.voice.demo_page import PAGE, SDK

    assert "@elevenlabs/client@1." in SDK and SDK in PAGE  # a version somebody saw work
    assert 'fetch("demo/call"' in PAGE and "demo_pass" in PAGE
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
