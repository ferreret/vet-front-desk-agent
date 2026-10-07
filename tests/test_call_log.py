"""Every call written down: what was said, what was done and what the model cost."""

import asyncio
import json
from datetime import datetime, timedelta

import pytest

pytest.importorskip("aiohttp")
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from vetdesk.agent import FrontDeskAgent  # noqa: E402
from vetdesk.agent.agent import Turn  # noqa: E402
from vetdesk.agent.tools import ToolEvent  # noqa: E402
from vetdesk.dbguard import ForeignDatabaseError  # noqa: E402
from vetdesk.kb import load_kb  # noqa: E402
from vetdesk.llm import Reply, ToolCall, Usage  # noqa: E402
from vetdesk.llm.scripted import ScriptedClient  # noqa: E402
from vetdesk.scheduling import SqliteAgenda  # noqa: E402
from vetdesk.voice.call_log import CallLog  # noqa: E402
from vetdesk.voice.endpoint import Switchboard, build_app  # noqa: E402

NOW = datetime(2026, 11, 3, 10, 15)  # a Tuesday morning
KEY, ADMIN = "a-test-key", "an-admin-key"
PROMPT = "vetdesk-conversation: conv_9\nvetdesk-caller: +34600111222"
HANG_UP = [{"type": "function", "function": {"name": "end_call", "description": "End.",
                                             "parameters": {"type": "object"}}}]


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        self.now += timedelta(seconds=5)
        return self.now


def _turn(text, tokens=(1000, 20), cached=0, tools=()):
    events = tuple(ToolEvent(name, {"asked": "x"}, {}, not ok) for name, ok in tools)
    return Turn(text, events, Usage(tokens[0] - cached, tokens[1], cached), 1 + len(tools),
                (0.4,) * (1 + len(tools)), 0.5)


def test_a_call_is_kept_line_by_line_with_what_it_cost(tmp_path):
    path = tmp_path / "state" / "calls.db"
    path.parent.mkdir()
    calls = CallLog(path, Clock())
    calls.begin("c1", "+34600111222", "gemini-3.5-flash-lite", "Planeta Animal, dígame.")
    calls.begin("c1", "+34600111222", "gemini-3.5-flash-lite", "Planeta Animal, dígame.")
    calls.turn("c1", 1, "Hola, soy Marta Pons", _turn("¿Sus apellidos?"), 0.0004, "es")
    calls.turn("c1", 2, "Pons Ribas, una cita para Luna",
               _turn("El lunes a las diez.", (2000, 30), 1500,
                     (("identify_client", True), ("book_appointment", False))),
               0.0007, "ca", "confirmed", "PONS RIBAS, MARTA")
    calls.happened("c1", "booked")
    calls.note("c1", "hung_up")
    calls.wait()

    reopened = CallLog(path, Clock())  # what a restart of the server finds
    (call,) = reopened.calls()
    assert (call["id"], call["caller"], call["language"]) == ("c1", "+34600111222", "ca")
    assert (call["identity"], call["client"]) == ("confirmed", "PONS RIBAS, MARTA")
    assert call["happened"] == ["booked"] and call["turns"] == 2
    assert call["tokens_in"] == 3000 and call["tokens_cached"] == 1500
    assert call["tokens_out"] == 50 and call["dollars"] == pytest.approx(0.0011)
    assert call["seconds"] == 30  # from when it began to the last line of it
    lines = reopened.call("c1")["lines"]
    assert [(line["heard"], line["said"], line["note"]) for line in lines] == [
        (None, "Planeta Animal, dígame.", "greeting"),  # once, however often it is begun
        ("Hola, soy Marta Pons", "¿Sus apellidos?", None),
        ("Pons Ribas, una cita para Luna", "El lunes a las diez.", None),
        (None, None, "hung_up")]
    assert lines[2]["tools"] == [
        {"name": "identify_client", "arguments": {"asked": "x"}, "ok": True},
        {"name": "book_appointment", "arguments": {"asked": "x"}, "ok": False}]
    assert (lines[2]["first_words"], lines[2]["requests"]) == (0.5, 3)
    assert reopened.totals() == {"calls": 1, "turns": 2, "requests": 4, "tokens_in": 3000,
                                 "tokens_cached": 1500, "tokens_out": 50,
                                 "dollars": pytest.approx(0.0011)}
    assert reopened.call("nobody") is None


def test_an_answer_taken_back_is_kept_and_marked_because_it_was_paid_for():
    calls = CallLog(now=Clock())
    calls.begin("c1", None, "m")
    calls.turn("c1", 1, "Hola, buen día.", _turn("Buenos días."), 0.001, "es")
    calls.taken_back("c1", 1)
    calls.turn("c1", 1, "Hola, bon dia.", _turn("Bon dia."), 0.001, "ca")
    calls.wait()
    call = calls.call("c1")
    assert [(line["heard"], line["taken_back"]) for line in call["lines"]] == [
        ("Hola, buen día.", 1), ("Hola, bon dia.", 0)]
    assert call["dollars"] == pytest.approx(0.002) and call["taken_back"] == 1


def test_old_calls_are_dropped_and_newer_ones_come_first(tmp_path):
    path, clock = tmp_path / "calls.db", Clock()
    calls = CallLog(path, clock)
    calls.begin("old", None, "m")
    clock.now += timedelta(days=80)
    calls.begin("newer", None, "m")
    calls.turn("newer", 1, "Hola", _turn("Dígame."), None, "es")  # a model with no price
    calls.wait()
    assert [call["id"] for call in calls.calls()] == ["newer", "old"]
    assert calls.call("newer")["lines"][0]["dollars"] is None
    clock.now += timedelta(days=20)
    assert [call["id"] for call in CallLog(path, clock, keep_days=90).calls()] == ["newer"]


def test_a_database_that_is_not_ours_is_never_opened(tmp_path):
    import sqlite3
    path = tmp_path / "somebody.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE clients (name TEXT)")
    with pytest.raises(ForeignDatabaseError):
        CallLog(path)


# --- through the server ---------------------------------------------------------------------

def _server(steps, clinic, calls, admin=ADMIN):
    kb = load_kb()
    agent = FrontDeskAgent(ScriptedClient(steps), clinic, kb, SqliteAgenda(kb, lambda: NOW),
                           lambda: NOW)
    return build_app(Switchboard(agent.start_call), KEY, "gemini-3.5-flash-lite",
                     admin_key=admin, calls=calls)


OPENING = "Clínica veterinaria Planeta Animal, dígame."  # the platform's own first message


def _messages(*said, opening=OPENING):
    messages = [{"role": "system", "content": PROMPT}]
    if opening:
        messages.append({"role": "assistant", "content": opening})
    for index, text in enumerate(said):
        if index:
            messages.append({"role": "assistant", "content": "(heard)"})
        messages.append({"role": "user", "content": text})
    return messages


def _talk(app, *requests, then=()):
    """Chat requests one after another, and then what the record's own addresses answer."""
    async def run():
        async with TestClient(TestServer(app)) as client:
            for messages in requests:
                response = await client.post(
                    "/v1/chat/completions",
                    json={"model": "x", "stream": True, "messages": messages, "tools": HANG_UP},
                    headers={"Authorization": f"Bearer {KEY}"})
                await response.text()
            answers = []
            for path, key in then:
                response = await client.get(
                    path, headers={"Authorization": f"Bearer {key}"} if key else {})
                answers.append((response.status, await response.text()))
            return answers

    return asyncio.run(run())


def test_a_call_through_the_server_is_written_down_and_read_back_with_the_key(clinic):
    lookup = ToolCall("c1", "get_availability", {"date_from": "2026-11-09",
                                                 "date_to": "2026-11-13", "part_of_day": "any"})
    steps = [Reply("", [lookup], usage=Usage(900, 12)),
             Reply("Tengo el lunes a las diez.", usage=Usage(1100, 15)),
             Reply("De nada. Que tenga un buen día.", usage=Usage(1200, 9))]
    calls = CallLog(now=Clock())
    app = _server(steps, clinic, calls)
    asked = _messages("¿Tenéis hora la semana que viene?")
    bye = _messages("¿Tenéis hora la semana que viene?", "Vale, gracias. Adiós.")
    listed, one, missing, no_key, wrong_key, page = _talk(app, asked, bye, then=[
        ("/calls", ADMIN), ("/calls/conv_9", ADMIN), ("/calls/conv_0", ADMIN),
        ("/calls", ""), ("/calls/conv_9", KEY), ("/calls/view", "")])

    assert listed[0] == 200
    listed = json.loads(listed[1])
    (call,) = listed["calls"]
    assert (call["id"], call["caller"], call["model"]) == (
        "conv_9", "+34600111222", "gemini-3.5-flash-lite")
    assert (call["turns"], call["requests"], call["tokens_in"]) == (2, 3, 3200)
    # 3200 tokens in and 36 out, at the model's price per million.
    assert call["dollars"] == pytest.approx((3200 * 0.30 + 36 * 2.50) / 1_000_000)
    assert listed["totals"]["calls"] == 1 and listed["totals"]["dollars"] == call["dollars"]

    lines = json.loads(one[1])["lines"]
    # The greeting kept is the one the caller heard, the platform's, and not the agent's own.
    assert (lines[0]["note"], lines[0]["said"]) == ("greeting", OPENING)
    assert [(line["heard"], line["said"], line["note"]) for line in lines[1:]] == [
        ("¿Tenéis hora la semana que viene?", "Tengo el lunes a las diez.", None),
        ("Vale, gracias. Adiós.", "De nada. Que tenga un buen día.", None),
        (None, None, "hung_up")]
    assert [tool["name"] for tool in lines[1]["tools"]] == ["get_availability"]
    assert missing[0] == 404
    # Reading a call back takes the clinic's own key: not none, and not the platform's.
    assert no_key[0] == 401 and wrong_key[0] == 401
    assert page[0] == 200 and "<title>Llamadas</title>" in page[1]


def test_the_greeting_written_down_is_ours_only_when_we_said_it(clinic):
    calls = CallLog(now=Clock())
    app = _server([Reply("Abrimos a las diez.")], clinic, calls)
    _talk(app, _messages("¿A qué hora abrís?", opening=""))  # how it was answered: not told
    calls.wait()
    assert [line["note"] for line in calls.call("conv_9")["lines"]] == [None]

    calls = CallLog(now=Clock())
    app = _server([Reply("Abrimos a las diez.")], clinic, calls)
    _talk(app, _messages(opening=""), _messages("¿A qué hora abrís?", opening=""))
    calls.wait()
    first = calls.call("conv_9")["lines"][0]  # asked to open the call: the agent's greeting
    assert first["note"] == "greeting" and first["said"].endswith("¿En qué puedo ayudarle?")


def test_without_a_key_of_its_own_the_record_cannot_be_read_at_all(clinic):
    app = _server([Reply("Dígame.")], clinic, CallLog(now=Clock()), admin="")
    (listed,) = _talk(app, then=[("/calls", "")])
    assert listed[0] == 404


def test_silences_and_a_line_heard_twice_are_told_apart_in_the_record(clinic):
    steps = [Reply("Buenos días, dígame."), Reply("Bon dia, digui'm."),
             Reply("Abrimos a las nueve y media.")]
    calls = CallLog(now=Clock())
    app = _server(steps, clinic, calls)
    first = _messages("Hola, buen día.")
    again = _messages("Hola, bon dia.")  # the same turn, written again a moment later
    quiet = _messages("Hola, bon dia.", "...")
    _talk(app, first, again, quiet)
    calls.wait()
    lines = calls.call("conv_9")["lines"]
    assert [(line["heard"], line["note"], line["taken_back"]) for line in lines[1:]] == [
        ("Hola, buen día.", None, 1), ("Hola, bon dia.", None, 0), (None, "silence", 0)]
    assert lines[3]["said"] and lines[3]["requests"] == 0  # a stock phrase: no model, no cost
