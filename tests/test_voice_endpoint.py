"""The agent behind a chat-completions address, as a voice platform would call it."""

import asyncio
import json
from datetime import datetime

import pytest

pytest.importorskip("aiohttp")
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from vetdesk.agent import FrontDeskAgent  # noqa: E402
from vetdesk.kb import load_kb  # noqa: E402
from vetdesk.llm import Reply, ToolCall  # noqa: E402
from vetdesk.llm.scripted import ScriptedClient  # noqa: E402
from vetdesk.scheduling import SqliteAgenda  # noqa: E402
from vetdesk.voice.endpoint import Switchboard, build_app  # noqa: E402

NOW = datetime(2026, 11, 3, 10, 15)
KEY = "a-test-key"
PROMPT = "conversation: conv_123\ncaller: {caller}"
LOOKUP = ToolCall("c1", "get_availability", {"date_from": "2026-11-09", "date_to": "2026-11-13",
                                             "part_of_day": "any"})


@pytest.fixture(scope="module")
def kb():
    return load_kb()


def _front_desk(steps, clinic, kb):
    model = ScriptedClient(steps)
    agent = FrontDeskAgent(model, clinic, kb, SqliteAgenda(kb, lambda: NOW), lambda: NOW)
    calls = []

    def start_call(number):
        calls.append(number)
        return agent.start_call(number)

    return model, calls, build_app(Switchboard(start_call), KEY)


def _messages(*said, caller="", prompt=PROMPT):
    messages = [{"role": "system", "content": prompt.format(caller=caller)}]
    for index, text in enumerate(said):
        if index:
            messages.append({"role": "assistant", "content": "(what the platform heard us say)"})
        messages.append({"role": "user", "content": text})
    return messages


def _ask(app, *requests, key=KEY):
    """Send chat requests one after another; the text and status of each answer."""
    async def run():
        async with TestClient(TestServer(app)) as client:
            answers = []
            for messages in requests:
                response = await client.post(
                    "/v1/chat/completions", json={"model": "x", "stream": True,
                                                  "messages": messages},
                    headers={"Authorization": f"Bearer {key}"} if key else {})
                answers.append((response.status, response.headers.get("Content-Type"),
                                await response.text()))
            return answers

    return asyncio.run(run())


def _spoken(stream: str) -> str:
    lines = [line[6:] for line in stream.splitlines() if line.startswith("data: ")]
    assert lines[-1] == "[DONE]"
    chunks = [json.loads(line) for line in lines[:-1]]
    assert all(chunk["object"] == "chat.completion.chunk" for chunk in chunks)
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"
    return "".join(chunk["choices"][0]["delta"].get("content", "") for chunk in chunks)


def test_the_agent_answers_in_the_format_a_voice_platform_expects(clinic, kb):
    model, calls, app = _front_desk([Reply("Abrimos a las nueve y media.")], clinic, kb)
    [(status, content_type, stream)] = _ask(app, _messages("¿A qué hora abrís?"))
    assert (status, content_type) == (200, "text/event-stream")
    assert _spoken(stream) == "Abrimos a las nueve y media."
    assert model.transcript.user_messages == ["¿A qué hora abrís?"]
    assert calls == [None]  # no number: a call from the web


def test_nothing_is_answered_without_the_key(clinic, kb):
    for key in (None, "wrong", ""):
        model, calls, app = _front_desk([Reply("Hola.")], clinic, kb)
        [(status, _, body)] = _ask(app, _messages("Hola"), key=key)
        assert status == 401 and "invalid key" in body
        assert calls == [] and model.transcript.user_messages == []


def test_the_callers_number_reaches_the_agent(clinic, kb):
    model, calls, app = _front_desk([Reply("Dígame.")], clinic, kb)
    _ask(app, _messages("Hola", caller="+34 618 065 507"))
    assert calls == ["+34618065507"] and "+34618065507" in model.transcript.context


def test_one_conversation_is_one_call_however_often_it_is_resent(clinic, kb):
    """The platform sends the whole conversation every time; the agent keeps its own."""
    steps = [Reply("¿Para qué animal?"), Reply("", (LOOKUP,), "tool_calls"),
             Reply("Tengo hueco el lunes.")]
    model, calls, app = _front_desk(steps, clinic, kb)
    first = _messages("Quiero una cita")
    second = _messages("Quiero una cita", "Para mi perra Kira")
    answers = _ask(app, first, second)
    assert [_spoken(stream) for _, _, stream in answers] == [
        "¿Para qué animal?", "Un momento, por favor. Tengo hueco el lunes."]
    assert calls == [None]  # one call, not two
    assert model.transcript.user_messages == ["Quiero una cita", "Para mi perra Kira"]


def test_a_line_asked_for_twice_is_answered_once(clinic, kb):
    """A retry or a resend must not run the tools again: a booking is made once."""
    steps = [Reply("", (LOOKUP,), "tool_calls"), Reply("Tengo hueco el lunes.")]
    model, _, app = _front_desk(steps, clinic, kb)
    request = _messages("Quiero una cita")
    answers = _ask(app, request, request)
    assert _spoken(answers[0][2]) == _spoken(answers[1][2]) == \
        "Un momento, por favor. Tengo hueco el lunes."
    assert model.transcript.user_messages == ["Quiero una cita"]
    assert len(model.transcript.tool_results) == 1


def test_two_conversations_are_two_calls(clinic, kb):
    model, calls, app = _front_desk([Reply("Dígame."), Reply("Digui.")], clinic, kb)
    other = _messages("Bon dia", prompt="conversation: conv_456\ncaller: {caller}")
    answers = _ask(app, _messages("Hola"), other)
    assert [_spoken(stream) for _, _, stream in answers] == ["Dígame.", "Digui."]
    assert len(calls) == 2


def test_without_an_id_from_the_platform_the_opening_tells_calls_apart(clinic, kb):
    model, calls, app = _front_desk([Reply("Uno."), Reply("Dos."), Reply("Tres.")], clinic, kb)
    plain = "You are a helpful assistant."
    unfilled = "conversation: {{{{system__conversation_id}}}}\ncaller: {caller}"
    _ask(app, _messages("Hola", prompt=plain), _messages("Hola", "¿Abrís?", prompt=plain),
         _messages("Buenas", prompt=unfilled))
    assert len(calls) == 2  # the same opening twice, then another one


def test_asked_to_open_the_call_the_agent_greets(clinic, kb):
    model, _, app = _front_desk([], clinic, kb)
    [(status, _, stream)] = _ask(app, [{"role": "system", "content": PROMPT.format(caller="")}])
    assert status == 200 and _spoken(stream).startswith("Clínica veterinaria Planeta Animal")
    assert model.transcript.user_messages == []


def test_a_request_that_is_not_a_chat_is_refused(clinic, kb):
    _, _, app = _front_desk([], clinic, kb)

    async def run():
        async with TestClient(TestServer(app)) as client:
            headers = {"Authorization": f"Bearer {KEY}"}
            bad = await client.post("/v1/chat/completions", json={"nothing": 1}, headers=headers)
            ok = await client.get("/health")
            return bad.status, ok.status

    assert asyncio.run(run()) == (400, 200)
