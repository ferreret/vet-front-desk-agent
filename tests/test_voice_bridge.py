"""The bridge from the agent's loop to a stream of speech. No voice library involved."""

import asyncio
import threading
import time
from datetime import datetime

import pytest

from vetdesk.agent import FrontDeskAgent
from vetdesk.kb import load_kb
from vetdesk.llm import LLMError, Reply, ToolCall
from vetdesk.llm.scripted import ScriptedClient
from vetdesk.scheduling import SqliteAgenda
from vetdesk.voice.bridge import STILL_THERE, TROUBLE, WAITING, Line, language_of

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


def _slow(reply, seconds=0.3):
    """A step of the script that takes its time, as a model on a bad afternoon does."""
    def step(transcript):
        time.sleep(seconds)
        return reply
    return step


def _waited(call, heard, patience, language=None):
    async def collect():
        return [piece async for piece in Line(call, patience).answer(heard, language)]

    return asyncio.run(collect())


def test_the_waiting_phrase_is_said_by_the_clock(clinic, kb):
    """Not by what the agent is doing: said before every tool, a caller heard it six times
    in a call; said only before the agenda, working out who was calling left four seconds
    of silence."""
    slow = ScriptedClient([_slow(Reply("", (LOOKUP,), "tool_calls")), Reply("Tengo el lunes.")])
    pieces = _waited(_call(slow, clinic, kb), "Quiero una cita", patience=0.05)
    assert pieces == [WAITING["es"] + " ", "Tengo el lunes."]

    # In time, nothing is added: neither to a plain answer nor to a quick look at the agenda.
    quick = ScriptedClient([Reply("", (LOOKUP,), "tool_calls"), Reply("Tengo el lunes.")])
    assert _waited(_call(quick, clinic, kb), "Quiero una cita", patience=5) == ["Tengo el lunes."]

    # A slow answer with no tool at all is covered too.
    thinking = ScriptedClient([_slow(Reply("¿Me dice su nombre y sus dos apellidos?"))])
    pieces = _waited(_call(thinking, clinic, kb), "Quiero una cita", patience=0.05)
    assert pieces == [WAITING["es"] + " ", "¿Me dice su nombre y sus dos apellidos?"]


def test_the_waiting_phrase_is_in_the_callers_language(clinic, kb):
    """ElevenLabs asks for answers and says nothing of the language heard: a caller who
    speaks Catalan must not be told "un momento, por favor"."""
    slow = ScriptedClient([_slow(Reply("Tinc dilluns al matí."))])
    call = _call(slow, clinic, kb)
    pieces = _waited(call, "Bon dia, voldria demanar hora.", patience=0.05)
    assert pieces[0] == WAITING["ca"] + " " and call.language == "ca"
    # A platform that does report the language heard is believed.
    told = ScriptedClient([_slow(Reply("I have Monday."))])
    assert _waited(_call(told, clinic, kb), "Hello", 0.05, "en")[0] == WAITING["en"] + " "


def test_an_answer_asked_for_again_repeats_the_waiting_phrase_it_was_given(clinic, kb):
    slow = ScriptedClient([_slow(Reply("Tengo el lunes."))])
    line = Line(_call(slow, clinic, kb), patience=0.05)

    async def twice():
        first = [piece async for piece in line.answer("Quiero una cita", turn=1)]
        again = [piece async for piece in line.answer("Quiero una cita", turn=1)]
        return first, again

    first, again = asyncio.run(twice())
    assert first == again == [WAITING["es"] + " ", "Tengo el lunes."]


def test_a_line_heard_again_a_moment_later_is_answered_as_last_written(clinic, kb):
    """Heard on a call in Catalan: the recogniser handed "Hola, buen día." over, then "Hola,
    bon dia." half a second later, and the platform kept the second. The first answer is
    taken back, as if it had never been asked for: the caller is answered in Catalan."""
    model = ScriptedClient([Reply("Buenos días. ¿En qué puedo ayudarle?"),
                            Reply("Bon dia. En què el puc ajudar?"), Reply("Digui'm el nom.")])
    now = [100.0]
    call = _call(model, clinic, kb)
    line = Line(call, patience=None, clock=lambda: now[0])

    async def play():
        first = [piece async for piece in line.answer("Hola, buen día.", turn=1)]
        now[0] += 0.5
        again = [piece async for piece in line.answer("Hola, bon dia.", turn=1)]
        now[0] += 0.2
        same = [piece async for piece in line.answer("Hola, bon dia.", turn=1)]
        now[0] += 6
        following = [piece async for piece in line.answer("Vull una cita.", turn=2)]
        return first, again, same, following

    first, again, same, following = asyncio.run(play())
    assert first == ["Buenos días. ¿En qué puedo ayudarle?"]
    assert again == same == ["Bon dia. En què el puc ajudar?"]
    assert following == ["Digui'm el nom."] and call.language == "ca"
    # The model was never told "Hola, buen día.": its conversation holds the line once.
    heard = model.transcript.user_messages
    assert len(heard) == 2 and heard[0].startswith("Hola, bon dia.\n\n(Note from the phone")
    assert heard[1] == "Vull una cita."


def test_a_line_heard_again_after_a_tool_ran_keeps_its_answer(clinic, kb):
    """A phone number came as "934879642." and then as nine words. The booking was made on
    the first: it is not taken back, and what was said about it is said again."""
    model = ScriptedClient([Reply("", (LOOKUP,), "tool_calls"), Reply("Tengo el lunes.")])
    now = [100.0]
    call = _call(model, clinic, kb)
    line = Line(call, patience=None, clock=lambda: now[0])

    async def play():
        first = [piece async for piece in line.answer("La semana que viene.", turn=3)]
        now[0] += 0.9
        return first, [piece async for piece in line.answer("La setmana que ve.", turn=3)]

    first, again = asyncio.run(play())
    assert first == again == ["Tengo el lunes."]
    assert model.transcript.user_messages == ["La semana que viene."]
    assert len(model.transcript.tool_results) == 1 and not call.take_back()


def test_a_line_can_be_taken_back_once_and_only_if_no_tool_ran(clinic, kb):
    model = ScriptedClient([Reply("Dígame."), Reply("Digui."), Reply("", (LOOKUP,), "tool_calls"),
                            Reply("Tinc dilluns.")])
    call = _call(model, clinic, kb)
    assert not call.take_back()  # nothing said yet
    call.say("Hola, bon dia.")
    assert call.language == "ca" and call.take_back()
    assert call.language == "es" and model.transcript.user_messages == []
    assert not call.take_back()  # once
    call.say("Hola, bon dia.")
    call.say("Vull una cita la setmana que ve.")
    assert not call.take_back() and len(model.transcript.user_messages) == 2


def test_other_words_for_the_same_turn_long_after_are_a_new_line(clinic, kb):
    """Only a moment makes it the same line: a turn number met again later is not trusted."""
    model = ScriptedClient([Reply("Dígame."), Reply("Abrimos a las diez.")])
    now = [100.0]
    line = Line(_call(model, clinic, kb), patience=None, clock=lambda: now[0])

    async def call():
        first = [piece async for piece in line.answer("Hola.", turn=1)]
        now[0] += 10
        return first, [piece async for piece in line.answer("¿A qué hora abrís?", turn=1)]

    assert asyncio.run(call()) == (["Dígame."], ["Abrimos a las diez."])


def test_silence_is_not_a_line_for_the_model(clinic, kb):
    """The platform sends "..." when the caller has gone quiet. Handed to the model, it was
    answered out loud with "(No response needed; the user has hung up or finished the
    call.)". It gets a stock question in the language of the call, and the model nothing."""
    model = ScriptedClient([Reply("Bon dia. En què el puc ajudar?"), Reply("Digui'm el nom.")])
    call = _call(model, clinic, kb)
    line = Line(call, patience=None)

    async def play():
        hello = [piece async for piece in line.answer("Hola, bon dia.", turn=1)]
        quiet = [piece async for piece in line.answer("...", turn=2)]
        spoke = [piece async for piece in line.answer("Vull una cita.", turn=2)]
        return hello, quiet, spoke

    hello, quiet, spoke = asyncio.run(play())
    assert quiet == [STILL_THERE["ca"]] and spoke == ["Digui'm el nom."]
    assert len(model.transcript.user_messages) == 2  # the two lines; the silence is not one
    assert _spoken(_call(ScriptedClient([]), clinic, kb), " … ")[0] == [STILL_THERE["es"]]


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


def test_every_language_has_both_stock_phrases():
    assert set(WAITING) == set(TROUBLE) == {"es", "ca", "en", "fr", "de", "nl", "it"}
