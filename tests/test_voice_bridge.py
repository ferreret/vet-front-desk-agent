"""The bridge from the agent's loop to a stream of speech. No voice library involved."""

import asyncio
import threading
from datetime import datetime

import pytest

from vetdesk.agent import FrontDeskAgent
from vetdesk.kb import load_kb
from vetdesk.llm import LLMError, Reply, ToolCall
from vetdesk.llm.scripted import ScriptedClient
from vetdesk.scheduling import SqliteAgenda
from vetdesk.voice.bridge import TROUBLE, WAITING, Line, language_of

NOW = datetime(2026, 11, 3, 10, 15)
LOOKUP = ToolCall("c1", "get_availability", {"date_from": "2026-11-09", "date_to": "2026-11-13",
                                             "part_of_day": "any"})


@pytest.fixture(scope="module")
def kb():
    return load_kb()


def _call(model, clinic, kb):
    agent = FrontDeskAgent(model, clinic, kb, SqliteAgenda(kb, lambda: NOW), lambda: NOW)
    return agent.start_call(None)


def _spoken(call, heard, language="es"):
    async def collect():
        turns = []
        pieces = [piece async for piece in Line(call).answer(heard, language, turns.append)]
        return pieces, turns

    return asyncio.run(collect())


def test_the_answer_comes_out_piece_by_piece(clinic, kb):
    model = ScriptedClient([Reply("Abrimos a las nueve y media.")])
    pieces, turns = _spoken(_call(model, clinic, kb), "¿A qué hora abrís?")
    assert pieces == ["Abrimos a las nueve y media."]
    assert turns[0].text == "Abrimos a las nueve y media." and turns[0].events == ()
    assert model.transcript.user_messages == ["¿A qué hora abrís?"]


def test_what_is_said_before_a_tool_reaches_the_caller_while_the_turn_is_still_running(clinic, kb):
    """The waiting phrase must be on its way to the caller's ear before the model is asked
    again, not handed over with the rest of the answer."""
    heard_first = threading.Event()
    in_time = []

    def after_the_tool(transcript):
        # The turn is still running here, on its worker thread. The listener, on the event
        # loop, must already have the first piece: wait for it instead of racing it.
        in_time.append(heard_first.wait(timeout=5))
        return Reply("Tengo hueco el lunes.")

    model = ScriptedClient([Reply("Un momento, lo miro.", (LOOKUP,), "tool_calls"),
                            after_the_tool])
    call = _call(model, clinic, kb)

    async def listen():
        pieces = []
        async for piece in Line(call).answer("Quiero una cita"):
            pieces.append(piece)
            heard_first.set()
        return pieces

    assert asyncio.run(listen()) == ["Un momento, lo miro.", " Tengo hueco el lunes."]
    assert in_time == [True]


def test_a_tool_called_in_silence_gets_a_waiting_phrase(clinic, kb):
    """Measured: in a third of the turns that use a tool the model says nothing first."""
    silent = [Reply("", (LOOKUP,), "tool_calls"), Reply("Tengo hueco el lunes.")]
    pieces, turns = _spoken(_call(ScriptedClient(list(silent)), clinic, kb), "Quiero una cita")
    assert pieces == [WAITING["es"], " Tengo hueco el lunes."]
    assert turns[0].text == f"{WAITING['es']} Tengo hueco el lunes."

    pieces, _ = _spoken(_call(ScriptedClient(list(silent)), clinic, kb), "Vull una cita", "ca")
    assert pieces[0] == WAITING["ca"]

    # A model that speaks for itself is not spoken over, and text mode has no filler.
    talks = ScriptedClient([Reply("Lo miro.", (LOOKUP,), "tool_calls"), Reply("El lunes.")])
    assert _spoken(_call(talks, clinic, kb), "Quiero una cita")[0] == ["Lo miro.", " El lunes."]
    assert _call(ScriptedClient(list(silent)), clinic, kb).say("Quiero una cita").text == \
        "Tengo hueco el lunes."


def test_a_broken_model_does_not_leave_the_caller_in_silence(clinic, kb):
    def fails(transcript):
        raise LLMError("529: overloaded", retryable=True)

    pieces, turns = _spoken(_call(ScriptedClient([fails]), clinic, kb), "Hola")
    assert pieces == [TROUBLE["es"]] and turns == []
    assert _spoken(_call(ScriptedClient([fails]), clinic, kb), "Hola", "ca")[0] == [TROUBLE["ca"]]


def test_the_model_runs_off_the_event_loop(clinic, kb):
    """A model call takes seconds; the voice pipeline must keep listening meanwhile."""
    threads = []

    def where(transcript):
        threads.append(threading.current_thread())
        return Reply("Hola.")

    _spoken(_call(ScriptedClient([where]), clinic, kb), "Hola")
    assert threads[0] is not threading.main_thread()


def test_an_interrupted_answer_still_finishes_its_turn(clinic, kb):
    """The caller talks over the agent: the answer stops being heard, the turn runs on, and
    the next answer waits for it. The model never gets two turns at once."""
    model = ScriptedClient([Reply("Un momento.", (LOOKUP,), "tool_calls"),
                            Reply("Tengo hueco el lunes."), Reply("Dígame.")])
    call = _call(model, clinic, kb)

    async def talked_over():
        line = Line(call)
        stream = line.answer("Quiero una cita")
        first = await anext(stream)
        await stream.aclose()  # the caller interrupts
        return first, [piece async for piece in line.answer("Perdona, otra cosa")]

    first, second = asyncio.run(talked_over())
    assert (first, second) == ("Un momento.", ["Dígame."])
    assert len(call.session.events) == 1  # the tool ran and its result reached the model
    assert model.transcript.user_messages == ["Quiero una cita", "Perdona, otra cosa"]


def test_tools_run_on_the_worker_thread(clinic, kb):
    """The agenda is opened where the call starts and used where the turn runs."""
    model = ScriptedClient([Reply("", (LOOKUP,), "tool_calls"), Reply("El lunes.")])
    call = _call(model, clinic, kb)
    _spoken(call, "Quiero una cita")
    assert [event.is_error for event in call.session.events] == [False]


@pytest.mark.parametrize(("code", "language"), [
    ("ca", "ca"), ("cat", "ca"), ("ca-ES", "ca"), ("es", "es"), ("spa", "es"), ("en-GB", "en"),
    ("eng", "en"), ("fra", "fr"), ("de", "de"), ("nld", "nl"), ("ita", "it"),
    ("pt", "es"), (None, "es"),  # a language with no stock phrase: the clinic's own
])
def test_the_language_to_speak_follows_what_was_heard(code, language):
    assert language_of(code) == language


def test_without_a_language_from_the_platform_the_stock_phrases_follow_the_caller(clinic, kb):
    """ElevenLabs asks for answers and says nothing of the language heard: a caller who
    speaks Catalan must not be told "un momento, por favor"."""
    model = ScriptedClient([Reply("", (LOOKUP,), "tool_calls"), Reply("Tinc dilluns al matí.")])
    call = _call(model, clinic, kb)

    async def run():
        return [piece async for piece in Line(call).answer("Bon dia, voldria demanar hora.")]

    assert asyncio.run(run())[0] == WAITING["ca"]
    assert call.language == "ca"


def test_every_language_has_both_stock_phrases():
    assert set(WAITING) == set(TROUBLE) == {"es", "ca", "en", "fr", "de", "nl", "it"}
