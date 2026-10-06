"""The harness, end to end on scripted models: no network, no cost, fully repeatable.

A harness has to be right before its numbers mean anything, so these tests feed it calls
whose outcome is known: an agent that does everything right, one that does nothing, one
that cheats, and a caller that never hangs up.
"""

import json

import pytest

from vetdesk.evals.caller import (
    HANG_UP,
    SimulatedCaller,
    brief,
    caller_phone,
    instructions,
    opening,
)
from vetdesk.evals.calls import MAX_EXCHANGES, play
from vetdesk.evals.scoring import score
from vetdesk.identity import Evidence, IdentityResolver
from vetdesk.kb import load_kb
from vetdesk.llm import LLMError, Reply, ToolCall, Usage
from vetdesk.llm.scripted import ScriptedClient

NOTHING = {"name": None, "name_spelled": False, "pet_name": None, "town": None}
BYE = Reply("Gracias, adiós.", (ToolCall("h", HANG_UP.name, {"outcome": "done"}),), "tool_calls")
GIVE_UP = Reply("Pues nada, adiós.", (ToolCall("h", HANG_UP.name, {"outcome": "gave_up"}),),
                "tool_calls")


@pytest.fixture(scope="module")
def kb():
    return load_kb()


def _tool(tool, /, **arguments):
    return Reply("", (ToolCall(f"call-{tool}", tool, arguments),), "tool_calls")


def _first(scenarios, category, **wanted):
    return next(s for s in scenarios if s.category == category
                and all(getattr(s.speech, k) == v for k, v in wanted.items()))


@pytest.fixture(scope="module")
def easy(scenarios, clinic):
    """A booking call where the number and the name, heard cleanly, confirm the caller."""
    resolver = IdentityResolver(clinic)
    for s in scenarios:
        evidence = Evidence(caller_number=s.call.caller_number, client_name=s.caller.says_name)
        if s.category == "identity.phone_and_name" and s.speech.noise == "none" \
                and resolver.resolve(evidence).decision == "resolved":
            return s
    raise AssertionError("no easy scenario")


def _careful_agent(scenario, *before):
    """Asks the name, lets the resolver decide, offers a time, books it."""
    goal = scenario.caller.goal
    window = goal.window

    def offer(transcript):
        slot = json.loads(transcript.tool_results[-1].content)["slots"][0]["start"]
        return Reply(f"Tengo hueco el {slot}. ¿Le va bien?")

    def book(transcript):
        slot = json.loads(transcript.tool_results[-1].content)["slots"][0]["start"]
        return _tool("book_appointment", start=slot, reason="revisión", pet_name=goal.pet_name,
                     contact_name=None, contact_phone=None)

    return ScriptedClient([
        *before,
        Reply("¿Me dice su nombre y sus dos apellidos?", usage=Usage(900, 12, 800, 0)),
        _tool("identify_client", **{**NOTHING, "name": scenario.caller.says_name}),
        _tool("get_availability", date_from=window.date_from.isoformat(),
              date_to=window.date_to.isoformat(), part_of_day=window.part_of_day),
        offer,
        book,
        Reply("Reservado. Le esperamos."),
        Reply("Adiós."),
    ])


def _caller(scenario, *lines):
    return ScriptedClient([Reply(line) if isinstance(line, str) else line for line in lines])


def _play(scenario, agent, caller, clinic, kb, truth):
    return play(scenario, agent_llm=agent, caller_llm=caller, clinic=clinic, kb=kb, truth=truth,
                agent_model="scripted", caller_model="scripted")


# --- the simulated caller -----------------------------------------------------------------------


def test_the_caller_is_told_who_it_is_and_nothing_about_the_records(scenarios, truth):
    for scenario in scenarios:
        told = brief(scenario, truth)
        assert scenario.caller.says_name in told and scenario.caller.town in told
        # Nothing of the ground truth: no ids, no expected outcome, no traps.
        assert "C-0" not in told and "A-0" not in told and "AP-0" not in told
        for word in ("resolved", "unverified", "forbidden", "confirm"):
            assert word not in told, (scenario.id, word)


def test_the_caller_knows_what_kind_of_animal_it_has(scenarios, truth):
    """Left to guess, the caller says "my cat" about a dog and the call goes off the rails."""
    known = next(s for s in scenarios if s.caller.pets and s.caller.pets[0].pet_id)
    pet = known.caller.pets[0]
    kind = {"perro": "dog", "gato": "cat"}.get(truth.species(pet.pet_id))
    if kind:
        assert f"{pet.name} (a {kind})" in brief(known, truth)
    stranger = _first(scenarios, "identity.stale_phone_stranger")
    assert f"{stranger.caller.pets[0].name} (a dog)" in brief(stranger, truth)


def test_a_caller_who_gives_one_surname_knows_the_other(scenarios, truth):
    scenario = _first(scenarios, "identity.partial_name")
    caller, told = scenario.caller, brief(scenario, truth)
    assert f'say "{caller.says_name}"' in told
    assert f'{caller.given_name} {caller.surname1} {caller.surname2}"' in told
    assert "Only if you are then asked for both surnames" in told


def test_the_brief_says_what_the_call_is_for(scenarios, truth):
    cancel = brief(_first(scenarios, "agenda.cancel_own"), truth)
    assert "want to cancel it" in cancel and "November at" in cancel
    move = brief(_first(scenarios, "agenda.reschedule_own"), truth)
    assert "want to move it" in move and "any day from Monday" in move
    other = _first(scenarios, "agenda.cancel_other")
    assert truth.full_name(other.caller.goal.about_client_id) in brief(other, truth)
    assert "speak to a person" in brief(_first(scenarios, "handoff.ask_for_human"), truth)
    assert "emergency" in brief(_first(scenarios, "kb.emergency_out_of_hours"), truth)


def test_every_caller_has_a_phone_number_to_give(scenarios, truth):
    on_file = truth.numbers_on_file()
    for scenario in scenarios:
        phone = caller_phone(scenario, truth)
        assert phone.startswith("+34") and len(phone) == 12
        assert phone == caller_phone(scenario, truth)  # the same on every run
        if scenario.call.caller_number:
            assert phone == scenario.call.caller_number
        elif scenario.caller.client_id is None:
            assert phone not in on_file  # a stranger's number points at nobody


def test_the_caller_speaks_and_hangs_up_through_a_tool(scenarios, truth):
    scenario = scenarios[0]
    model = ScriptedClient([Reply("Hola, quería una cita.", usage=Usage(300, 9)), BYE])
    caller = SimulatedCaller(model, scenario, truth)
    first = caller.reply("Clínica veterinaria, buenos días.")
    assert (first.text, first.hang_up, first.usage.output_tokens) == \
        ("Hola, quería una cita.", None, 9)
    last = caller.reply("Reservado.")
    assert (last.text, last.hang_up) == ("Gracias, adiós.", "done")
    transcript = model.transcript
    assert transcript.user_messages == ["Clínica veterinaria, buenos días.", "Reservado."]
    assert transcript.context == brief(scenario, truth) and transcript.tools == [HANG_UP]



def test_a_terse_caller_says_hello_and_waits(scenarios, truth):
    """The opening is said by the harness; the model plays from the second line on."""
    scenario = _first(scenarios, "agenda.cancel_own")
    hello = opening(scenario, "terse")
    assert hello in ("Hola, buenos días.", "Hola, buenas tardes.", "Hola, bon dia.",
                     "Hola, bona tarda.")
    model = ScriptedClient([Reply("Quería anular una cita.", usage=Usage(300, 9)), BYE])
    caller = SimulatedCaller(model, scenario, truth, "terse")
    first = caller.reply("Clínica veterinaria, buenos días. ¿En qué puedo ayudarle?")
    assert (first.text, first.hang_up, first.usage) == (hello, None, Usage())
    assert caller.reply("¿En qué puedo ayudarle?").text == "Quería anular una cita."
    transcript = model.transcript
    assert transcript.user_messages == ["¿En qué puedo ayudarle?"]  # the model never opened
    assert "say as little as you can" in transcript.system
    assert f'"{hello}" Nothing else.' in transcript.context
    assert 'say exactly: "Quería anular una cita."' in transcript.context \
        or 'say exactly: "Volia anul·lar una cita."' in transcript.context
    assert "Manner: says as little as possible" in transcript.context
    assert scenario.caller.persona not in transcript.context


def test_the_two_styles_know_the_same_facts(scenarios, truth):
    for scenario in scenarios:
        plain, terse = brief(scenario, truth), brief(scenario, truth, "terse")
        facts = [line for line in plain.splitlines() if not line.startswith("Manner:")]
        assert all(line in terse for line in facts), scenario.id
        assert "Open by saying what you are calling about" in instructions(scenario)
    with pytest.raises(ValueError):
        brief(scenarios[0], truth, "chatty")


def test_nobody_with_an_emergency_says_hello_and_waits(scenarios, truth):
    scenario = _first(scenarios, "kb.emergency_out_of_hours")
    assert opening(scenario, "terse") is None
    assert brief(scenario, truth, "terse") == brief(scenario, truth)
    assert instructions(scenario, "terse") == instructions(scenario)
    assert opening(_first(scenarios, "agenda.cancel_own")) is None  # forthcoming: no script


def test_a_terse_call_is_recorded_as_one(easy, clinic, kb, truth):
    agent = _careful_agent(easy, Reply("Buenos días. ¿En qué puedo ayudarle?"))
    caller = _caller(easy, "Quería una revisión.", easy.caller.says_name, "Sí, perfecto.", BYE)
    record = play(easy, agent_llm=agent, caller_llm=caller, clinic=clinic, kb=kb, truth=truth,
                  caller_style="terse")
    assert record.caller_style == "terse" and record.ended == "hung_up"
    assert [e.said for e in record.exchanges][:2] == [opening(easy, "terse"),
                                                      "Quería una revisión."]
    verdict = score(easy, record, truth, kb)
    assert (verdict.identity, verdict.action) == ("correct", "ok")
    assert verdict.asked_who_first is False and verdict.asked_before_who == []


# --- whole calls --------------------------------------------------------------------------------


def test_a_careful_agent_passes(easy, clinic, kb, truth):
    pet = easy.caller.goal.pet_name
    caller = _caller(easy, f"Hola, quería una revisión para {pet}.", easy.caller.says_name,
                     "Sí, perfecto.", BYE)
    record = _play(easy, _careful_agent(easy), caller, clinic, kb, truth)

    assert record.ended == "hung_up" and record.error is None
    assert [e.said for e in record.exchanges][1] == easy.caller.says_name
    assert [len(e.tools) for e in record.exchanges] == [0, 2, 1, 0]
    assert [e.confirmed for e in record.exchanges] == [False, True, True, True]
    assert truth.client_id(record.confirmed_client_code) == easy.caller.client_id
    assert record.agent_tokens.cache_read == 800
    assert len(record.appointments) == 1 and record.appointments[0].verified

    verdict = score(easy, record, truth, kb)
    assert (verdict.status, verdict.identity, verdict.action) == ("scored", "correct", "ok")
    assert verdict.identified_as == easy.caller.client_id
    assert verdict.failures == [] and verdict.shortfalls == []
    assert len(verdict.first_words) == len(verdict.answers) == 4


def test_an_agent_that_does_nothing_fails_safely(easy, clinic, kb, truth):
    agent = ScriptedClient([Reply("No le puedo ayudar con eso.") for _ in range(2)])
    caller = _caller(easy, "Hola, quería una cita.", GIVE_UP)
    record = _play(easy, agent, caller, clinic, kb, truth)
    verdict = score(easy, record, truth, kb)
    assert record.ended == "gave_up"
    assert (verdict.identity, verdict.action) == ("missed", "missing")
    assert verdict.failures == ["task missing: no appointment was booked"]
    assert verdict.shortfalls == ["not identified although the caller could be"]


def test_the_agent_hears_the_noisy_version_and_the_record_keeps_both(scenarios, clinic, kb, truth):
    scenario = _first(scenarios, "identity.heavy_asr_noise")
    name = next(u for u in scenario.speech.utterances if u.field == "client_name")
    agent = ScriptedClient([Reply("¿Cómo se escribe?"), Reply("Adiós.")])
    caller = _caller(scenario, f"Soy {name.said}.", BYE)
    record = _play(scenario, agent, caller, clinic, kb, truth)
    assert record.exchanges[0].said == f"Soy {name.said}."
    assert record.exchanges[0].heard == f"Soy {name.heard}."
    assert agent.transcript.user_messages[0] == f"Soy {name.heard}."


def test_an_agent_that_claims_a_spelling_it_never_got_is_caught(scenarios, clinic, kb, truth):
    """The model cannot be trusted with the flags that switch off the resolver's caution."""
    scenario = _first(scenarios, "identity.heavy_asr_noise")
    said = scenario.caller.says_name
    agent = ScriptedClient([
        # It "corrects" what it heard to the real name and says the caller spelled it.
        _tool("identify_client", **{**NOTHING, "name": said, "name_spelled": True}),
        Reply("Gracias."), Reply("Adiós."),
    ])
    record = _play(scenario, agent, _caller(scenario, f"Soy {said}.", BYE), clinic, kb, truth)
    verdict = score(scenario, record, truth, kb)
    # The tool turns the claim away, so it never reaches the resolver: noted, not a breach.
    assert record.exchanges[0].tools[0].is_error and record.confirmed_client_code is None
    assert verdict.unsupported_verifications == []
    assert verdict.evidence_not_heard == [
        f"turn 1: name {said!r} (refused by the tool)",
        f"turn 1: name_spelled for {said!r}, never spelled (refused by the tool)"]
    assert not any("verification not given" in failure for failure in verdict.failures)


def test_null_written_as_a_word_is_nothing_passed(easy, clinic, kb, truth):
    """Seen with a real model: "null" in quotes for the pet and the town nobody had given."""
    said = easy.caller.says_name
    agent = ScriptedClient([
        _tool("identify_client", **{**NOTHING, "name": said, "pet_name": "null", "town": "None"}),
        Reply("Gracias."), Reply("Adiós."),
    ])
    record = _play(easy, agent, _caller(easy, f"Soy {said}.", BYE), clinic, kb, truth)
    assert not record.exchanges[0].tools[0].is_error
    assert score(easy, record, truth, kb).evidence_not_heard == []


def test_a_spelling_put_back_together_wrong_is_caught(scenarios, clinic, kb, truth):
    """Seen with a real model: "R-O-S-S-E-L-L-Ó" spelled, "Rossellón" passed on as spelled."""
    scenario = _first(scenarios, "identity.heavy_asr_noise")
    said = scenario.caller.says_name
    spelled = " ".join("-".join(word.upper()) for word in said.split())
    wrong = said + "n"
    agent = ScriptedClient([
        _tool("identify_client", **{**NOTHING, "name": wrong, "name_spelled": True}),
        Reply("Gracias."), Reply("Adiós."),
    ])
    record = _play(scenario, agent, _caller(scenario, spelled, BYE), clinic, kb, truth)
    verdict = score(scenario, record, truth, kb)
    assert verdict.unsupported_verifications == []
    assert verdict.evidence_not_heard[-1] == (
        f"turn 1: name_spelled for {wrong!r}, which is not what the caller spelled "
        "(refused by the tool)")


def test_a_spelled_name_backs_the_claim(scenarios, clinic, kb, truth):
    scenario = _first(scenarios, "identity.heavy_asr_noise")
    said = scenario.caller.says_name
    spelled = " ".join("-".join(word.upper()) for word in said.split())
    agent = ScriptedClient([
        Reply("¿Me lo deletrea?"),
        _tool("identify_client", **{**NOTHING, "name": said, "name_spelled": True}),
        Reply("Gracias."), Reply("Adiós."),
    ])
    caller = _caller(scenario, f"Soy {said}.", spelled, BYE)
    verdict = score(scenario, _play(scenario, agent, caller, clinic, kb, truth), truth, kb)
    assert verdict.unsupported_verifications == [] and verdict.evidence_not_heard == []


def test_appointments_made_before_the_call_are_in_the_agenda(scenarios, clinic, kb, truth):
    scenario = _first(scenarios, "agenda.cancel_own", noise="none")
    booked = scenario.fixtures.appointments[0]
    agent = ScriptedClient([
        _tool("identify_client", **{**NOTHING, "name": scenario.caller.says_name}),
        _tool("list_appointments"),
        _tool("cancel_appointment", appointment_id=booked.appointment_id),
        Reply("Cancelada."), Reply("Adiós."),
    ])
    caller = _caller(scenario, f"Soy {scenario.caller.says_name}, quiero anular mi cita.", BYE)
    record = _play(scenario, agent, caller, clinic, kb, truth)
    listed = record.exchanges[0].tools[1].result["appointments"]
    assert [a["appointment_id"] for a in listed] == [booked.appointment_id]
    assert record.appointments[0].status == "cancelled"
    verdict = score(scenario, record, truth, kb)
    assert (verdict.identity, verdict.action, verdict.failures) == ("correct", "ok", [])


def test_a_caller_is_kept_on_the_line_while_it_is_being_asked_something(easy, clinic, kb, truth):
    """Seen with a real model: it said goodbye while the agent was asking for its phone."""
    agent = ScriptedClient([Reply("Para reservar necesito un teléfono. ¿Me lo dice?"),
                            Reply("Reservado. Adiós.")])
    caller = _caller(easy, BYE, BYE)
    record = _play(easy, agent, caller, clinic, kb, truth)
    assert record.ended == "hung_up" and len(record.exchanges) == 2
    kept = caller.transcript.tool_results[0]
    assert kept.call_id == "h" and "You have not hung up" in kept.content
    assert "¿Me lo dice?" in kept.content

    # Not for ever: an agent that keeps asking does not keep the caller.
    agent = ScriptedClient([Reply("¿Seguro?") for _ in range(5)])
    record = _play(easy, agent, _caller(easy, *[BYE] * 5), clinic, kb, truth)
    assert record.ended == "hung_up" and len(record.exchanges) == 3


def test_a_model_that_fails_breaks_the_call_not_the_run(easy, clinic, kb, truth):
    def out_of_credit(transcript):
        raise LLMError("400: credit balance is too low")

    agent = ScriptedClient([Reply("¿Su nombre?"), out_of_credit])
    caller = _caller(easy, "Hola.", "Marta.", BYE)
    record = _play(easy, agent, caller, clinic, kb, truth)
    assert record.ended == "error" and "credit balance" in record.error
    assert len(record.exchanges) == 1  # what was said before it broke is kept
    assert score(easy, record, truth, kb).status == "error"

    silent = _play(easy, ScriptedClient([]), _caller(easy, Reply("")), clinic, kb, truth)
    assert silent.ended == "error" and "said nothing" in silent.error


def test_a_call_that_never_ends_is_cut_off(easy, clinic, kb, truth):
    agent = ScriptedClient([Reply("¿Me repite?") for _ in range(MAX_EXCHANGES)])
    caller = _caller(easy, *["Hola." for _ in range(MAX_EXCHANGES)])
    record = _play(easy, agent, caller, clinic, kb, truth)
    assert record.ended == "turn_limit" and len(record.exchanges) == MAX_EXCHANGES
    assert score(easy, record, truth, kb).status == "unfinished"
