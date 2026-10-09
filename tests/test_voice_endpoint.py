"""The agent behind a chat-completions address, as a voice platform would call it."""

import asyncio
import hashlib
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


def _tool_call(stream: str, after: str = "") -> dict | None:
    """The tool call an answer consists of, if it is one: (name, arguments), and no words
    but those said ahead of it (`after`)."""
    chunks = [json.loads(line[6:]) for line in stream.splitlines()
              if line.startswith("data: ") and line != "data: [DONE]"]
    calls = [call for chunk in chunks
             for call in chunk["choices"][0]["delta"].get("tool_calls", [])]
    if not calls:
        return None
    assert chunks[-1]["choices"][0]["finish_reason"] == "tool_calls"
    words = [chunk["choices"][0]["delta"].get("content") or "" for chunk in chunks]
    assert "".join(words) == after
    assert not any(words[words.index(after) + 1:]) if after else True  # said first
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


HANG_UP = [{"type": "function", "function": {"name": "end_call", "description": "End.",
                                             "parameters": {"type": "object"}}}]


def test_when_the_goodbyes_are_said_the_platform_is_told_to_hang_up(clinic, kb):
    """Heard on a call: "Have a good day", and the line stayed open until the caller hung up
    or thirty seconds went by. Our address says goodbye; only the platform can hang up."""
    steps = [Reply("Abrimos a las nueve y media."), Reply("De nada. Que tenga un buen día."),
             Reply("¿Para qué día la quiere?")]
    model, _, app = _front_desk(steps, clinic, kb)
    asked = _messages("¿A qué hora abrís?")
    bye = _messages("¿A qué hora abrís?", "Vale, eso es todo, gracias. Adiós.")
    answers = [stream for _, _, stream in _ask(app, asked, bye, tools=HANG_UP)]
    assert _tool_call(answers[0]) is None and _spoken(answers[0]) == "Abrimos a las nueve y media."
    # The goodbye is said by us, ahead of the tool: on a phone call the platform hung up
    # without saying the farewell it had been handed with it.
    assert _tool_call(answers[1], after="De nada. Que tenga un buen día.") == {
        "name": "end_call", "reason": "the caller and the agent have said goodbye"}
    # Heard on a call from the demo's page: "De nada, buenos días." and the line stayed
    # open. The time of day wished to a caller who is done is a goodbye; given back at the
    # start of an answer it is a greeting.
    steps = [Reply("De nada, buenos días."), Reply("Buenos días. Abrimos a las nueve y media.")]
    _, _, app = _front_desk(steps, clinic, kb)
    done = _messages("No, eso es todo. Gracias.")
    _, _, other = _front_desk(steps[1:], clinic, kb)
    (_, _, stream), = _ask(app, done, tools=HANG_UP)
    assert _tool_call(stream, after="De nada, buenos días.")["name"] == "end_call"
    (_, _, stream), = _ask(other, _messages("Buenos días, nada más quería saber el horario."),
                           tools=HANG_UP)
    assert _tool_call(stream) is None
    # Heard on another: asked "¿Necesita algo más?", the caller said "Vale, gracias. No,
    # no necesito nada", the agent wished a good afternoon, and the line stayed open. No
    # to that question may end a call; the same words to another question do not.
    for asked, hangs_up in (("Abrimos a las diez. ¿Necesita algo más?", True),
                            ("¿Quiere que le apunte para el lunes?", False)):
        _, _, app = _front_desk([Reply("De nada. ¡Que tenga una buena tarde!")], clinic, kb)
        told = [*_messages("¿A qué hora abrís?"), {"role": "assistant", "content": asked},
                {"role": "user", "content": "Vale, gracias. No, no necesito nada."}]
        (_, _, stream), = _ask(app, told, tools=HANG_UP)
        bye = "De nada. ¡Que tenga una buena tarde!"
        if hangs_up:
            assert _tool_call(stream, after=bye)["name"] == "end_call"
        else:
            assert _tool_call(stream) is None and _spoken(stream) == bye
    # A goodbye that the agent does not take for one ends nothing: it is said, as ever.
    model, _, app = _front_desk([Reply("¿Para qué día la quiere?")], clinic, kb)
    (_, _, stream), = _ask(app, _messages("Adiós, digo, quería una cita."), tools=HANG_UP)
    assert _tool_call(stream) is None and _spoken(stream) == "¿Para qué día la quiere?"


def test_to_a_caller_who_has_only_said_goodbye_anything_that_asks_nothing_is_one(clinic, kb):
    """Heard on a call: "No, eso es todo. Gracias.", "De nada. ¡Que vaya muy bien!", and
    the line stayed open: the third farewell in two days that was on no list of words."""
    for bye in ("De nada. ¡Que vaya muy bien!", "A usted. ¡Cuídese!", "Un placer."):
        _, _, app = _front_desk([Reply(bye)], clinic, kb)
        for said in ("No, eso es todo. Gracias.", "Nada más, muchas gracias. Adiós."):
            _, _, app = _front_desk([Reply(bye)], clinic, kb)
            (_, _, stream), = _ask(app, _messages(said), tools=HANG_UP)
            assert _tool_call(stream, after=bye)["name"] == "end_call", (said, bye)
    # A line that closes and asks for something has not only said goodbye: the answer to
    # it ends nothing, unless it is a farewell in so many words.
    answer = "Abrimos a las nueve y media."
    for said in ("Buenos días, nada más quería saber el horario.",
                 "Eso es todo, ¿y a qué hora abrís mañana?",
                 "Nada más, bueno, dígame el horario de mañana."):
        _, _, app = _front_desk([Reply(answer)], clinic, kb)
        (_, _, stream), = _ask(app, _messages(said), tools=HANG_UP)
        assert _tool_call(stream) is None and _spoken(stream) == answer, said
    # Nor when the agent did something in answer to it, or asks something.
    look = ToolCall("a", "get_availability", {
        "date_from": "2026-11-09", "date_to": "2026-11-09", "part_of_day": "morning"})
    _, _, app = _front_desk([Reply("", (look,), "tool_calls"), Reply("Tengo el lunes.")],
                            clinic, kb)
    (_, _, stream), = _ask(app, _messages("Nada más, gracias."), tools=HANG_UP)
    assert _tool_call(stream) is None and _spoken(stream) == "Tengo el lunes."
    _, _, app = _front_desk([Reply("¿Seguro que no necesita nada más?")], clinic, kb)
    (_, _, stream), = _ask(app, _messages("Nada más, gracias."), tools=HANG_UP)
    assert _tool_call(stream) is None


def test_silence_after_the_goodbyes_hangs_up_and_a_first_silence_does_not(clinic, kb):
    model, _, app = _front_desk([Reply("Abrimos a las nueve y media.")], clinic, kb)
    asked = _messages("¿A qué hora abrís?")
    quiet = _messages("¿A qué hora abrís?", "...")
    still = _messages("¿A qué hora abrís?", "...", "...")
    answers = [stream for _, _, stream in _ask(app, asked, quiet, still, tools=HANG_UP)]
    assert _tool_call(answers[1]) is None and _spoken(answers[1]) == "¿Sigue ahí?"
    assert _tool_call(answers[2], after="Gracias por llamar. Adiós.")["name"] == "end_call"
    assert len(model.transcript.user_messages) == 1  # silence never reached the model


def test_a_platform_that_cannot_hang_up_is_told_nothing_of_it(clinic, kb):
    model, _, app = _front_desk([Reply("De nada, adiós.")], clinic, kb)
    (_, _, stream), = _ask(app, _messages("Eso es todo, adiós."))
    assert _tool_call(stream) is None and _spoken(stream) == "De nada, adiós."


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
    assert turn["soft_timeout_config"] == {"timeout_seconds": 3.0, "message": "Mmm...",
                                           "use_llm_generated_message": False}
    assert turn["speculative_turn"] is False
    assert turn["silence_end_call_timeout"] == 30.0  # a line nobody is on is hung up


def test_whoever_picks_the_phone_up_says_it_is_not_a_person(monkeypatch, clinic):
    """Asked for by the first person from outside to try it: a caller is told at once."""
    from vetdesk.agent import FrontDeskAgent
    from vetdesk.agent.prompt import ANNOUNCED, system_prompt
    from vetdesk.kb import load_kb
    from vetdesk.llm.scripted import ScriptedClient
    from vetdesk.scheduling import SqliteAgenda
    from vetdesk.voice.elevenlabs_agent import config, demo_settings

    monkeypatch.setenv("VETDESK_TTS_VOICE", "voice")
    assert "inteligencia artificial" in ANNOUNCED
    phone = config("https://example.test", "secret")
    said = phone["conversation_config"]["agent"]["first_message"]
    assert said == f"Clínica veterinaria Planeta Animal. {ANNOUNCED}, dígame."
    demo = demo_settings(config("https://example.test", "secret"))
    assert demo["conversation_config"]["agent"]["first_message"] == said
    # And the same when it is our own agent that opens the call, by text.
    kb, now = load_kb(), datetime(2026, 11, 3, 10, 15)
    call = FrontDeskAgent(ScriptedClient([]), clinic, kb, SqliteAgenda(kb, lambda: now),
                          lambda: now).start_call(None)
    assert ANNOUNCED in call.greeting and "Never say or let them think" in system_prompt(kb)


def test_the_platform_is_asked_to_leave_out_voices_in_the_background(monkeypatch):
    from vetdesk.voice.elevenlabs_agent import config

    monkeypatch.setenv("VETDESK_TTS_VOICE", "voice")
    settings = config("https://example.test", "secret")["conversation_config"]
    assert settings["vad"] == {"background_voice_detection": True}


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
    assert tools["end_call"]["params"] == {"system_tool_type": "end_call"}
    assert tools["end_call"]["force_pre_tool_speech"] is True


def test_a_second_platform_is_looked_at_before_it_is_answered_for_real(clinic, kb):
    """Vapi's route begins as a way of seeing what it sends. Nothing of a caller's is kept,
    and no call by it reaches a model, an agenda or anybody's record."""
    from vetdesk.voice.demo import NO_PASS
    from vetdesk.voice.endpoint import platform_key, shape

    model = ScriptedClient([])  # asked anything, it would have no answer
    agent = FrontDeskAgent(model, clinic, kb, SqliteAgenda(kb, lambda: NOW), lambda: NOW)
    app = build_app(Switchboard(agent.start_call), KEY, admin_key="admin")
    sent = {"model": "vetdesk", "stream": True,
            "messages": [{"role": "system", "content": "Eres la recepción."},
                         {"role": "user", "content": "Soy Marta Soler, quiero una cita."}],
            "tools": [{"type": "function", "function": {"name": "endCall"}}],
            "call": {"id": "abc", "type": "webCall", "customer": {"number": "+34600111222"}}}

    async def run():
        async with TestClient(TestServer(app)) as client:
            refused = await client.post("/vapi/chat/completions", json=sent)
            ours = await client.post("/vapi/chat/completions", json=sent,
                                     headers={"Authorization": f"Bearer {KEY}"})
            answered = await client.post(
                "/vapi/chat/completions", json=sent,
                headers={"Authorization": f"Bearer {platform_key(KEY, 'vapi')}"})
            theirs = await client.post(
                "/v1/chat/completions", json={"model": "x", "messages": sent["messages"]},
                headers={"Authorization": f"Bearer {platform_key(KEY, 'vapi')}"})
            closed = await client.get("/vapi/seen")
            kept = await client.get("/vapi/seen", headers={"Authorization": "Bearer admin"})
            return (refused.status, await answered.text(), closed.status, await kept.json(),
                    ours.status, theirs.status)

    refused, answered, closed, kept, ours, theirs = asyncio.run(run())
    assert refused == 401 and closed == 401
    # Its key is its own: ours does not open its route, and its does not open ours, where
    # real calls come. The platform shows the key it holds to whoever reads the assistant.
    assert ours == 401 and theirs == 401
    assert _spoken(answered) == NO_PASS and model.transcript.user_messages == []
    assert [look["authorized"] for look in kept] == [False, False, True]
    assert kept[0]["authorization"]["length"] == 0  # none was sent
    assert kept[2]["authorization"] == {
        "scheme": "Bearer", "length": 64,
        "mark": hashlib.sha256(platform_key(KEY, "vapi").encode()).hexdigest()[:8]}
    assert platform_key(KEY, "vapi") not in json.dumps(kept) and KEY not in json.dumps(kept)
    text = json.dumps(kept, ensure_ascii=False)
    assert "Marta" not in text and "600111222" not in text and "recepción" not in text
    body = kept[2]["body"]
    assert body["call"] == {"id": "str(3)", "type": "webCall", "customer": {"number": "str(12)"}}
    assert body["messages"][1] == {"role": "user", "content": "str(33)"}
    assert body["tools"][0]["function"]["name"] == "endCall"
    assert shape(list(range(20)))[-1] == "... 8 more"


def test_the_second_platform_is_given_as_little_as_the_first(monkeypatch):
    """A voice, a way of hearing, and where to ask what to say: nothing of the agent's."""
    from vetdesk.agent.prompt import ANNOUNCED
    from vetdesk.voice.endpoint import platform_key
    from vetdesk.voice.vapi_agent import config

    monkeypatch.setenv("VETDESK_TTS_VOICE", "voice")
    monkeypatch.setenv("VETDESK_ENDPOINT_KEY", "the-platforms-key")
    settings = config("https://example.test/", "credential-1")
    model = settings["model"]
    assert (model["provider"], model["url"]) == ("custom-llm", "https://example.test/vapi")
    (prompt,) = model["messages"]
    assert prompt["content"].splitlines() == [
        "vetdesk-conversation: vapi-{{call.id}}", "vetdesk-caller: {{customer.number}}",
        "vetdesk-demo: {{demo_pass}}"]
    assert model["tools"] == [{"type": "endCall"}]
    assert ANNOUNCED in settings["firstMessage"]  # it says at once that it is not a person
    # The key is kept at the platform and named by its id; no key travels with the
    # assistant, ours least of all.
    assert (settings["credentials"], settings["credentialIds"]) == ([], ["credential-1"])
    assert "the-platforms-key" not in json.dumps(settings)
    assert platform_key("the-platforms-key", "vapi") not in json.dumps(settings)
    assert settings["maxDurationSeconds"] == 200
