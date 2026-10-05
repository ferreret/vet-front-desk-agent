"""The agent loop, driven by a scripted model: no network, no cost, fully repeatable."""

import json
from datetime import datetime

import pytest

from vetdesk.agent import FrontDeskAgent
from vetdesk.agent.agent import CANNOT_HELP, DID_NOT_FOLLOW, MAX_TOOL_ROUNDS
from vetdesk.agent.prompt import INSTRUCTIONS, system_prompt
from vetdesk.kb import load_kb
from vetdesk.llm import LLMError, Reply, ToolCall, Usage
from vetdesk.llm.scripted import ScriptedClient
from vetdesk.scheduling import SqliteAgenda

NOW = datetime(2026, 11, 3, 10, 15)
NOTHING = {"name": None, "name_spelled": False, "pet_name": None, "town": None}


@pytest.fixture(scope="module")
def kb():
    return load_kb()


def _call(model, clinic, kb, number=None, now=NOW):
    agent = FrontDeskAgent(model, clinic, kb, SqliteAgenda(kb, lambda: now), lambda: now)
    return agent.start_call(number)


def _tool(tool, /, **arguments):
    return Reply("", (ToolCall(f"call-{tool}", tool, arguments),), "tool_calls")


def test_a_turn_without_tools(clinic, kb):
    model = ScriptedClient([Reply("Abrimos de nueve y media a una y media.",
                                  usage=Usage(1200, 20, 1000, 0))])
    call = _call(model, clinic, kb)
    turn = call.say("¿A qué hora abrís?")
    assert turn.text == "Abrimos de nueve y media a una y media."
    assert (turn.events, turn.requests) == ((), 1)
    assert turn.usage.cache_read_tokens == 1000
    assert model.transcript.user_messages == ["¿A qué hora abrís?"]


def test_the_model_gets_the_clinic_facts_and_the_call_context(clinic, kb):
    model = ScriptedClient([Reply("Hola.")])
    call = _call(model, clinic, kb, "+34600111222")
    assert call.greeting == ("Clínica veterinaria Planeta Animal, buenos días. "
                             "¿En qué puedo ayudarle?")
    transcript = model.transcript
    assert "600 555 020" in transcript.system and kb.render() in transcript.system
    assert "+34600111222" in transcript.context and "martes" in transcript.context
    assert "The clinic is open" in transcript.context and call.greeting in transcript.context
    assert len(transcript.tools) == 8


def test_what_to_do_in_an_emergency_depends_on_the_hour_and_code_decides(kb):
    from vetdesk.agent.prompt import call_context

    open_now = call_context(kb, datetime(2026, 11, 3, 10, 15), None, "Hola")
    assert "come straight to the clinic" in open_now and "600 555 020" in open_now
    closed = call_context(kb, datetime(2026, 11, 8, 3, 20), None, "Hola")  # a Sunday night
    assert "The clinic is closed" in closed and "600 555 020" in closed
    assert "Do not tell them to come to the clinic" in closed
    assert "come straight" not in closed


def test_the_system_prompt_is_the_same_for_every_call(clinic, kb):
    first, second = ScriptedClient([]), ScriptedClient([])
    _call(first, clinic, kb, "+34600111222")
    _call(second, clinic, kb, None, datetime(2026, 11, 8, 3, 20))
    assert first.transcript.system == second.transcript.system == system_prompt(kb)
    assert "hidden" in second.transcript.context and "closed" in second.transcript.context
    assert "buenas noches" in _call(ScriptedClient([]), clinic, kb, None,
                                    datetime(2026, 11, 8, 3, 20)).greeting


def test_no_clinic_fact_is_written_into_the_instructions(kb):
    """Facts come from the knowledge base only, so the prompt cannot go stale."""
    for fact in (kb.emergency.phone, "600 555 020", "971 555 010", kb.clinic.address, "09:30",
                 "euros"):
        assert fact not in INSTRUCTIONS


def test_tools_run_between_the_models_steps(clinic, kb):
    model = ScriptedClient([
        Reply("Un momento, lo miro.",
              (ToolCall("c1", "get_availability",
                        {"date_from": "2026-11-09", "date_to": "2026-11-13",
                         "part_of_day": "morning"}),),
              "tool_calls"),
        lambda transcript: Reply(
            "Tengo el lunes a las " + json.loads(transcript.tool_results[0].content)
            ["slots"][0]["start"][-5:] + "."),
    ])
    turn = _call(model, clinic, kb).say("Quiero una cita la semana que viene por la mañana")
    assert turn.text == "Un momento, lo miro. Tengo el lunes a las 09:30."
    assert [event.name for event in turn.events] == ["get_availability"]
    assert turn.requests == 2


def test_a_model_that_reaches_for_client_data_too_early_gets_nothing(clinic, kb, scenarios):
    scenario = next(s for s in scenarios if s.category == "identity.phone_and_name")
    model = ScriptedClient([_tool("get_pets"), _tool("list_appointments"),
                            Reply("¿Me dice su nombre y sus dos apellidos?")])
    call = _call(model, clinic, kb, scenario.call.caller_number)
    turn = call.say("Hola, quería saber cuándo le toca la vacuna a mi perro")
    assert all(event.is_error for event in turn.events)
    for result in model.transcript.tool_results:
        assert result.is_error and "not confirmed" in result.content
    assert call.session.client is None


def test_identification_through_the_loop(clinic, kb, scenarios, client_ids):
    confirmed = 0
    for scenario in scenarios:
        if scenario.category != "identity.phone_and_name" or scenario.speech.noise != "none":
            continue
        model = ScriptedClient([
            _tool("identify_client", **{**NOTHING, "name": scenario.caller.says_name}),
            lambda t: Reply("status=" + json.loads(t.tool_results[-1].content)["status"]),
            _tool("get_pets"),
            lambda t: Reply("pets=" + ",".join(
                pet["name"] for pet in json.loads(t.tool_results[-1].content)["pets"])),
        ])
        call = _call(model, clinic, kb, scenario.call.caller_number)
        if call.say(f"Soy {scenario.caller.says_name}").text != "status=confirmed":
            continue  # a typo on file: this caller has to spell their name first
        confirmed += 1
        assert client_ids[call.session.client.code] == scenario.caller.client_id
        assert scenario.caller.pets[0].name in call.say("¿Qué animales tengo?").text
    assert confirmed


def test_a_runaway_model_is_stopped_and_made_to_speak(clinic, kb):
    looping = [_tool("get_pets") for _ in range(MAX_TOOL_ROUNDS + 1)]
    model = ScriptedClient([*looping, Reply("Perdone, ¿me repite su nombre?")])
    turn = _call(model, clinic, kb).say("Hola")
    assert turn.text == "Perdone, ¿me repite su nombre?"
    assert len(turn.events) == MAX_TOOL_ROUNDS  # the last round was answered, not run
    assert "Too many steps" in model.transcript.tool_results[-1].content

    model = ScriptedClient([_tool("get_pets") for _ in range(MAX_TOOL_ROUNDS + 2)])
    with pytest.raises(LLMError):
        _call(model, clinic, kb).say("Hola")


def test_the_agent_never_goes_silent(clinic, kb):
    assert _call(ScriptedClient([Reply("", stop="refusal")]), clinic, kb).say("x").text \
        == CANNOT_HELP
    assert _call(ScriptedClient([Reply("")]), clinic, kb).say("x").text == DID_NOT_FOLLOW
    cut = _call(ScriptedClient([Reply("Le cuento que", stop="max_tokens")]), clinic, kb)
    assert cut.say("x").text == "Le cuento que"


def test_every_turn_reports_how_long_the_model_took(clinic, kb):
    model = ScriptedClient([_tool("get_pets"), Reply("¿Me dice su nombre?")])
    turn = _call(model, clinic, kb).say("Hola")
    assert len(turn.latencies) == turn.requests == 2
    assert turn.seconds == sum(turn.latencies) >= 0


def test_the_latency_benchmark_plays_a_whole_call(clinic, kb, scenarios):
    from vetdesk.evals.latency import caller_lines, format_timings, time_call

    scenario = next(s for s in scenarios if s.category == "identity.hidden_number")
    lines = caller_lines(scenario)
    model = ScriptedClient([Reply("De acuerdo.", usage=Usage(1000, 10)) for _ in lines])
    timing = time_call("claude-haiku-4-5", model, clinic, kb, scenario)
    assert len(timing.answers) == len(timing.requests) == len(lines)
    assert model.transcript.user_messages == lines
    assert scenario.caller.town in lines[3] and not timing.booked and not timing.confirmed
    assert timing.cost == pytest.approx((6000 * 1.0 + 60 * 5.0) / 1_000_000)
    assert "claude-haiku-4-5" in format_timings([timing])


def test_what_the_agent_says_before_a_tool_is_heard_before_the_tool_runs(clinic, kb):
    heard = []

    def second_step(transcript):
        # By the time the tool has run, the waiting phrase has already been spoken.
        heard.append("<tool ran>")
        return Reply("Tengo hueco el lunes.")

    model = ScriptedClient([
        Reply("Un momento, lo miro.",
              (ToolCall("c1", "get_availability",
                        {"date_from": "2026-11-09", "date_to": "2026-11-13",
                         "part_of_day": "any"}),),
              "tool_calls"),
        second_step,
    ])
    turn = _call(model, clinic, kb).say("Quiero una cita", heard.append)
    assert heard == ["Un momento, lo miro.", "<tool ran>", " Tengo hueco el lunes."]
    assert turn.text == "".join(piece for piece in heard if piece != "<tool ran>")
    assert turn.first_words is not None and turn.first_words <= turn.seconds + 1


def test_fallback_lines_are_spoken_too(clinic, kb):
    heard = []
    _call(ScriptedClient([Reply("", stop="refusal")]), clinic, kb).say("x", heard.append)
    assert heard == [CANNOT_HELP]
