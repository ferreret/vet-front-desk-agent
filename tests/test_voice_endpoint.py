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
PROMPT = ("Task description: You are an AI agent. Your character definition is provided "
          "below. vetdesk-conversation: conv_123\nvetdesk-caller: {caller}\nGuardrails: none.")
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


def _ask(app, *requests, key=KEY, tools=None):
    """Send chat requests one after another; the text and status of each answer."""
    async def run():
        async with TestClient(TestServer(app)) as client:
            answers = []
            for messages in requests:
                response = await client.post(
                    "/v1/chat/completions",
                    json={"model": "x", "stream": True, "messages": messages,
                          **({"tools": tools} if tools else {})},
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


def test_a_real_phone_can_stand_in_for_one_of_the_made_up_clinics(clinic, kb):
    """Nobody's real phone is on file in an invented clinic. To try the usual call by voice,
    the server is told which real number calls as which of the clinic's."""
    from vetdesk.voice.endpoint import Switchboard, stand_ins

    assert stand_ins("") == {}
    pairs = stand_ins("600 111 222 = +34 612 000 001, 0034600333444=612000002")
    assert pairs == {"+34600111222": "+34612000001", "+34600333444": "+34612000002"}
    with pytest.raises(SystemExit):
        stand_ins("600111222")
    model = ScriptedClient([Reply("Dígame.")])
    agent = FrontDeskAgent(model, clinic, kb, SqliteAgenda(kb, lambda: NOW), lambda: NOW)
    calls = []

    def start_call(number):
        calls.append(number)
        return agent.start_call(number)

    app = build_app(Switchboard(start_call, stand_ins=pairs), KEY)
    _ask(app, _messages("Hola", caller="34600111222"))
    assert calls == ["+34612000001"] and "+34612000001" in model.transcript.context
    assert "600111222" not in model.transcript.context  # the real number goes no further


def test_one_conversation_is_one_call_however_often_it_is_resent(clinic, kb):
    """The platform sends the whole conversation every time; the agent keeps its own."""
    steps = [Reply("¿Para qué animal?"), Reply("", (LOOKUP,), "tool_calls"),
             Reply("Tengo hueco el lunes.")]
    model, calls, app = _front_desk(steps, clinic, kb)
    first = _messages("Quiero una cita")
    second = _messages("Quiero una cita", "Para mi perra Kira")
    answers = _ask(app, first, second)
    assert [_spoken(stream) for _, _, stream in answers] == [
        "¿Para qué animal?", "Tengo hueco el lunes."]
    assert calls == [None]  # one call, not two
    assert model.transcript.user_messages == ["Quiero una cita", "Para mi perra Kira"]


def test_a_line_asked_for_twice_is_answered_once(clinic, kb):
    """A retry or a resend must not run the tools again: a booking is made once."""
    steps = [Reply("", (LOOKUP,), "tool_calls"), Reply("Tengo hueco el lunes.")]
    model, _, app = _front_desk(steps, clinic, kb)
    request = _messages("Quiero una cita")
    answers = _ask(app, request, request)
    assert _spoken(answers[0][2]) == _spoken(answers[1][2]) == "Tengo hueco el lunes."
    assert model.transcript.user_messages == ["Quiero una cita"]
    assert len(model.transcript.tool_results) == 1


def test_a_line_sent_again_in_other_words_is_one_line(clinic, kb):
    """The platform's recogniser rewrites a line it has just sent and asks again: the same
    place in the conversation, other words. The model keeps one line, the last written."""
    model, _, app = _front_desk([Reply("Buenos días."), Reply("Bon dia.")], clinic, kb)
    answers = _ask(app, _messages("Hola, buen día."), _messages("Hola, bon dia."))
    assert [_spoken(stream) for _, _, stream in answers] == ["Buenos días.", "Bon dia."]
    assert len(model.transcript.user_messages) == 1
    assert model.transcript.user_messages[0].startswith("Hola, bon dia.")


def test_a_line_sent_again_after_a_tool_ran_is_not_run_again(clinic, kb):
    steps = [Reply("", (LOOKUP,), "tool_calls"), Reply("Tengo hueco el lunes.")]
    model, _, app = _front_desk(steps, clinic, kb)
    answers = _ask(app, _messages("La semana que viene."), _messages("La setmana que ve."))
    assert _spoken(answers[0][2]) == _spoken(answers[1][2]) == "Tengo hueco el lunes."
    assert model.transcript.user_messages == ["La semana que viene."]
    assert len(model.transcript.tool_results) == 1


CHANGE_LANGUAGE = [{"type": "function", "function": {
    "name": "language_detection", "description": "Change the conversation language.",
    "parameters": {"type": "object", "properties": {"reason": {"type": "string"},
                                                    "language": {"type": "string"}}}}}]


def _tool_call(stream: str) -> dict | None:
    """The tool call an answer consists of, if it is one: (name, arguments), no words."""
    chunks = [json.loads(line[6:]) for line in stream.splitlines()
              if line.startswith("data: ") and line != "data: [DONE]"]
    calls = [call for chunk in chunks
             for call in chunk["choices"][0]["delta"].get("tool_calls", [])]
    if not calls:
        return None
    assert chunks[-1]["choices"][0]["finish_reason"] == "tool_calls"
    assert not any(chunk["choices"][0]["delta"].get("content") for chunk in chunks)
    (call,) = calls
    return {"name": call["function"]["name"], **json.loads(call["function"]["arguments"])}


def test_the_platform_is_told_to_listen_in_the_callers_language(clinic, kb):
    """Set to Spanish, the platform's recogniser wrote a caller's Catalan as Spanish. The
    first line that tells Catalan is answered with the platform's own tool for changing
    language and nothing else; it changes, asks again, and gets the answer then."""
    steps = [Reply("Bon dia. En què el puc ajudar?"), Reply("Digui'm el nom."),
             Reply("¿Me dice su nombre?")]
    model, _, app = _front_desk(steps, clinic, kb)
    hello = _messages("Hola, bon dia.")
    told = [*hello,
            {"role": "assistant", "content": None, "tool_calls": [{"id": "call_1"}]},
            {"role": "tool", "tool_call_id": "call_1", "content": "Language changed to ca"}]
    later = [*told, {"role": "assistant", "content": "Bon dia."},
             {"role": "user", "content": "Vull una cita."}]
    back = [*later, {"role": "assistant", "content": "Digui'm el nom."},
            {"role": "user", "content": "Quiero una cita para mi perro, por favor."}]
    answers = [stream for _, _, stream in _ask(app, hello, told, later, back, back,
                                               tools=CHANGE_LANGUAGE)]
    assert _tool_call(answers[0])["name"] == "language_detection"
    assert _tool_call(answers[0])["language"] == "ca"
    assert _spoken(answers[1]) == "Bon dia. En què el puc ajudar?"  # worked out meanwhile
    assert _tool_call(answers[2]) is None and _spoken(answers[2]) == "Digui'm el nom."
    # The caller goes over to Spanish: the platform is told again, and asked again answers.
    assert _tool_call(answers[3])["language"] == "es"
    assert _tool_call(answers[4]) is None and _spoken(answers[4]) == "¿Me dice su nombre?"
    assert len(model.transcript.user_messages) == 3  # each line reached the model once


def test_a_platform_without_the_tool_is_told_nothing(clinic, kb):
    model, _, app = _front_desk([Reply("Bon dia.")], clinic, kb)
    (_, _, stream), = _ask(app, _messages("Hola, bon dia."))
    assert _tool_call(stream) is None and _spoken(stream) == "Bon dia."


def test_a_line_in_the_language_being_listened_in_changes_nothing(clinic, kb):
    model, _, app = _front_desk([Reply("Dígame."), Reply("¿Su nombre?")], clinic, kb)
    answers = _ask(app, _messages("Hola, buenos días."),
                   _messages("Hola, buenos días.", "Joan Feliu Plana."), tools=CHANGE_LANGUAGE)
    assert [_tool_call(stream) for _, _, stream in answers] == [None, None]
    assert [_spoken(stream) for _, _, stream in answers] == ["Dígame.", "¿Su nombre?"]


def test_two_conversations_are_two_calls(clinic, kb):
    model, calls, app = _front_desk([Reply("Dígame."), Reply("Digui.")], clinic, kb)
    other = _messages("Bon dia", prompt="vetdesk-conversation: conv_456\nvetdesk-caller: {caller}")
    answers = _ask(app, _messages("Hola"), other)
    assert [_spoken(stream) for _, _, stream in answers] == ["Dígame.", "Digui."]
    assert len(calls) == 2


def test_without_an_id_from_the_platform_the_opening_tells_calls_apart(clinic, kb):
    model, calls, app = _front_desk([Reply("Uno."), Reply("Dos."), Reply("Tres.")], clinic, kb)
    plain = "You are a helpful assistant."
    unfilled = "vetdesk-conversation: {{{{system__conversation_id}}}}\nvetdesk-caller: {caller}"
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


def test_no_waiting_phrase_of_ours_on_this_route(clinic, kb):
    """The platform fills its own silences; a phrase from here was only ever spoken late."""
    import time

    def slow(transcript):
        time.sleep(0.2)
        return Reply("Tengo hueco el lunes.")

    _, _, app = _front_desk([slow], clinic, kb)
    (_, _, stream), = _ask(app, _messages("Quiero una cita"))
    assert _spoken(stream) == "Tengo hueco el lunes."


def test_the_platform_is_asked_to_fill_long_waits_itself(monkeypatch):
    from vetdesk.voice.elevenlabs_agent import config

    monkeypatch.setenv("VETDESK_TTS_VOICE", "voice")
    turn = config("https://example.test", "secret")["conversation_config"]["turn"]
    assert turn["soft_timeout_config"] == {"timeout_seconds": 2.0, "message": "Mmm...",
                                           "use_llm_generated_message": False}
    assert turn["speculative_turn"] is False
    assert turn["silence_end_call_timeout"] == 30.0  # a line nobody is on is hung up


def test_the_platform_agent_can_be_told_to_change_language(monkeypatch):
    """It starts in Spanish and holds the other languages, so that our address can tell it
    which one to listen in. Its own model decides nothing: no model of theirs is used."""
    from vetdesk.voice.elevenlabs_agent import config

    monkeypatch.setenv("VETDESK_TTS_VOICE", "voice")
    monkeypatch.delenv("VETDESK_LANGUAGES", raising=False)
    settings = config("https://example.test", "secret")["conversation_config"]
    assert settings["agent"]["language"] == "es"
    assert list(settings["language_presets"]) == ["ca", "en", "de", "ru", "fr", "it"]
    tools = settings["agent"]["prompt"]["built_in_tools"]
    assert tools["language_detection"]["params"]["system_tool_type"] == "language_detection"
