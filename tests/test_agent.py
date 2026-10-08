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
    assert "600 555 020" in transcript.system and kb.render(NOW.date()) in transcript.system
    assert "+34600111222" in transcript.context and "martes" in transcript.context
    assert "The clinic is open" in transcript.context and call.greeting in transcript.context
    assert len(transcript.tools) == 8


def test_what_to_do_in_an_emergency_depends_on_the_hour_and_code_decides(kb):
    from vetdesk.agent.prompt import call_context

    open_now = call_context(kb, datetime(2026, 11, 3, 10, 15), None, "Hola")
    assert "venga directamente a la clínica" in open_now and "600 555 020" in open_now
    closed = call_context(kb, datetime(2026, 11, 8, 3, 20), None, "Hola")  # a Sunday night
    assert "The clinic is closed" in closed and "600 555 020" in closed
    assert "do not tell them to come to it" in closed
    assert "venga directamente" not in closed and "veterinario de guardia" in closed


def test_the_emergency_sentence_is_written_in_code_in_every_language(clinic, kb):
    """Left to the model to say in Italian, the emergency number came out in words, and
    wrong. Every language has the sentence, with the number in figures, for the open clinic
    and for the closed one; the model is given it in the language the call turns to."""
    from vetdesk.agent.prompt import EMERGENCY, emergency_sentence
    from vetdesk.language import SPOKEN

    night, morning = datetime(2026, 11, 8, 3, 20), datetime(2026, 11, 3, 10, 15)
    assert set(EMERGENCY) == set(SPOKEN)
    for language in SPOKEN:
        closed, opened = (emergency_sentence(kb, when, language) for when in (night, morning))
        assert "600 555 020" in closed and "600 555 020" in opened and closed != opened
    model = ScriptedClient([Reply("È un'urgenza.")])
    call = _call(model, clinic, kb, None, night)
    call.say("Buonasera, il mio cane ha mangiato del cioccolato!")
    told = model.transcript.user_messages[0]
    assert emergency_sentence(kb, night, "it") in told
    assert emergency_sentence(kb, night, "es") in model.transcript.context


def test_the_system_prompt_is_the_same_for_every_call_of_a_season(clinic, kb):
    first, second, summer = ScriptedClient([]), ScriptedClient([]), ScriptedClient([])
    _call(first, clinic, kb, "+34600111222")
    _call(second, clinic, kb, None, datetime(2026, 11, 8, 3, 20))
    assert first.transcript.system == second.transcript.system \
        == system_prompt(kb, NOW.date())
    # Only the day the opening hours change does it change, to say which are in force.
    _call(summer, clinic, kb, None, datetime(2026, 7, 8, 10, 0))
    assert summer.transcript.system != first.transcript.system
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
        == CANNOT_HELP["es"]
    assert _call(ScriptedClient([Reply("")]), clinic, kb).say("x").text == DID_NOT_FOLLOW["es"]
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


def test_a_turn_that_ends_on_a_waiting_phrase_is_made_to_go_on(clinic, kb):
    """"Un momento, lo miro", and nothing looked up: on the phone, a dead line."""
    from vetdesk.agent.agent import STILL_WAITING

    look = _tool("get_availability", date_from="2026-11-09", date_to="2026-11-13",
                 part_of_day="morning")
    model = ScriptedClient([Reply("Un momento, lo miro."), look,
                            Reply("Tengo el lunes a las nueve y media.")])
    call, heard = _call(model, clinic, kb), []
    turn = call.say("Quería una cita la semana que viene por la mañana.", heard.append)
    assert turn.text == "Un momento, lo miro. Tengo el lunes a las nueve y media."
    assert "".join(heard) == turn.text and turn.requests == 3
    assert [event.name for event in turn.events] == ["get_availability"]
    # The nudge reaches the model, and is not taken for something the caller said.
    assert model.transcript.user_messages[1] == STILL_WAITING
    assert "silence" not in call.session.lines_with and "caller" not in call.session.lines_with


def test_one_word_changes_the_language_only_while_the_call_is_settling(clinic, kb):
    """Measured with callers in French and Italian: "Le unghie." has a Spanish word in it and
    "Vaccination annuelle." an English one, and each carried its call off into the wrong
    language. A greeting may change the language on one word; later it takes two."""
    model = ScriptedClient([Reply("Buongiorno.") for _ in range(6)])
    call = _call(model, clinic, kb)
    call.say("Buongiorno.")
    assert call.language == "it"
    call.say("Vorrei prendere un appuntamento.")
    call.say("Le unghie.")                      # "le" tells Spanish; one word is not enough
    call.say("Toby.")
    assert call.language == "it" and call.hears("Sí.") == "it"
    call.say("Perdone, ¿me puede hablar en castellano, por favor?")
    assert call.language == "es"
    assert call.hears("Здравствуйте") == "ru"   # an alphabet is not a word: always enough


def test_set_phrases_reach_the_model_in_the_language_of_the_call_only(clinic, kb):
    """With the phrases of three languages in its instructions, the model asked a caller
    speaking Catalan "May I have your full name, please?". It is given one language's."""
    from vetdesk.agent.agent import LANGUAGE_NOTE
    from vetdesk.agent.prompt import PHRASES
    from vetdesk.language import SPOKEN

    assert set(PHRASES) == set(SPOKEN)
    model = ScriptedClient([Reply("Bon dia."), Reply("Good morning.")])
    call = _call(model, clinic, kb)
    every = [phrase for phrases in PHRASES.values() for phrase in phrases]
    assert not any(phrase in model.transcript.system for phrase in every)
    assert all(phrase in model.transcript.context for phrase in PHRASES["es"])
    assert not any(phrase in model.transcript.context for phrase in PHRASES["ca"] + PHRASES["en"])
    call.say("Hola, bon dia.")
    told = model.transcript.user_messages[0]
    assert all(phrase in told for phrase in PHRASES["ca"]) and LANGUAGE_NOTE["ca"][:-1] in told
    assert not any(phrase in told for phrase in PHRASES["en"] + PHRASES["es"])
    call.say("Sorry, do you speak English?")
    assert all(phrase in model.transcript.user_messages[1] for phrase in PHRASES["en"])
    assert call.language == call.session.language == "en"


def test_a_turn_that_runs_on_is_cut_short_and_made_to_go_on(clinic, kb):
    """Seen once: "Let me check the schedule. Let me look at the available times." and so on
    to the token limit, in answer to a caller speaking Spanish. Some three minutes of it."""
    from vetdesk.agent.agent import MAX_SPOKEN, STILL_WAITING

    babble = "Un momento, por favor. " + "Let me check the schedule. " * 150
    look = _tool("get_availability", date_from="2026-11-09", date_to="2026-11-13",
                 part_of_day="morning")

    def streamed(transcript):  # as a model writes it: piece by piece
        return Reply(babble.strip(), stop="max_tokens")

    model = ScriptedClient([streamed, look, Reply("Tengo el lunes a las nueve y media.")])
    call, heard = _call(model, clinic, kb), []
    turn = call.say("La semana que viene por la mañana.", heard.append)
    assert turn.text.endswith("Tengo el lunes a las nueve y media.")
    assert len(turn.text) < MAX_SPOKEN + 60 and turn.text.count("Let me check") < 30
    assert [event.name for event in turn.events] == ["get_availability"]
    assert model.transcript.user_messages[1] == STILL_WAITING
    assert "".join(heard).endswith("Tengo el lunes a las nueve y media.")
    assert len("".join(heard)) < MAX_SPOKEN + 60  # the voice was not handed the rest


def test_an_answer_of_ordinary_length_is_said_whole(clinic, kb):
    from vetdesk.agent.agent import MAX_SPOKEN

    long = ("Tengo el lunes 9 de noviembre a las nueve y media de la mañana, el lunes 9 de "
            "noviembre a las once y media de la mañana o el martes 10 de noviembre a las "
            "nueve y media de la mañana. ¿Cuál le va mejor?")
    assert len(long) < MAX_SPOKEN / 2
    assert _call(ScriptedClient([Reply(long)]), clinic, kb).say("Por la mañana.").text == long


def test_the_agent_is_nudged_once_and_only_when_left_waiting(clinic, kb):
    from vetdesk.agent.agent import left_waiting

    stubborn = ScriptedClient([Reply("Un moment, ho miro."), Reply("Un moment.")])
    turn = _call(stubborn, clinic, kb).say("Vull una cita.")
    assert turn.text == "Un moment, ho miro. Un moment." and turn.requests == 2
    assert left_waiting("Gracias, Lucía. Un momento, lo miro.")
    assert left_waiting("Ara mateix ho comprovo.")
    for answer in ("De nada, adiós.", "¿Me espera un momento?", "Abrimos de diez a una.",
                   "Un momento, lo miro. Tengo el lunes a las nueve y media, el martes a las "
                   "once o el miércoles a las diez."):
        assert not left_waiting(answer), answer
    plain = ScriptedClient([Reply("De nada, adiós.")])
    assert _call(plain, clinic, kb).say("Gracias.").requests == 1


def test_the_model_is_told_the_callers_language_when_their_words_show_it(clinic, kb):
    """Greeted in Catalan, a model went on in Spanish. Code tells the languages apart."""
    from vetdesk.agent.agent import LANGUAGE_NOTE, STILL_IN

    model = ScriptedClient([Reply("Bona tarda."), Reply("Digui'm."), Reply("Sí."),
                            Reply("Claro.")])
    call = _call(model, clinic, kb)
    assert call.language == "es"  # the clinic answers the phone in Spanish
    call.say("Hola, bona tarda.")
    sent = model.transcript.user_messages
    assert call.language == "ca"
    assert sent[0].startswith(f"Hola, bona tarda.\n\n{LANGUAGE_NOTE['ca'][:-1]}")
    assert "És una urgència" in sent[0] and sent[0].endswith(")")
    call.say("Voldria demanar hora per al meu gos.")
    call.say("Joan Feliu Plana.")  # a name tells nothing: the call stays in Catalan
    # ...and the model is reminded of it: given a name alone, one answered in the language
    # the name looked like.
    assert sent[1:] == ["Voldria demanar hora per al meu gos.",
                        f"Joan Feliu Plana.\n\n{STILL_IN['ca']}"]
    assert call.language == "ca"
    call.say("Perdone, mejor en castellano, por favor.")
    assert call.language == "es" and LANGUAGE_NOTE["es"][:-1] in sent[3]
    # The note is the phone system's: it is not what the caller said.
    assert "phone" not in call.session.lines_with and "system" not in call.session.lines_with
    # The agent's own lines follow the call's language too.
    refused = _call(ScriptedClient([Reply("", stop="refusal")]), clinic, kb)
    assert refused.say("Bon dia, vull una cosa estranya.").text == CANNOT_HELP["ca"]


def test_fallback_lines_are_spoken_too(clinic, kb):
    heard = []
    _call(ScriptedClient([Reply("", stop="refusal")]), clinic, kb).say("x", heard.append)
    assert heard == [CANNOT_HELP["es"]]


def test_once_a_call_has_got_going_the_question_that_opens_it_is_not_asked_again(clinic, kb):
    """Heard on a call: asked the opening hours and thanked ("Okey, gracias"), the agent
    answered "¿En qué puedo ayudarle?", as if picking up the phone. With a set phrase for
    "anything else" the model still wrote the other one in 4 answers of 48, so the one is
    put in place of the other as it is said."""
    model = ScriptedClient([
        Reply("Buenas tardes. ¿En qué puedo ayudarle?"),  # to a hello: asked as it should be
        Reply("¿En qué puedo ayudarle?"),  # and again, to a second hello: nothing done yet
        Reply("Los sábados abrimos de diez a una."),
        Reply("De nada. ¿En qué puedo ayudarle?"), Reply("De nada, ¿en qué puedo ayudarle?"),
        Reply("De res. En què el puc ajudar?")])
    call, heard = _call(model, clinic, kb), []
    answers = [call.say(line, heard.append).text for line in (
        "Hola, buenas tardes.", "Hola, ¿me oye?", "¿Abrís los sábados?", "Okey, gracias.",
        "De acuerdo.", "Molt bé, gràcies, però una cosa més.")]
    assert answers == [
        "Buenas tardes. ¿En qué puedo ayudarle?", "¿En qué puedo ayudarle?",
        "Los sábados abrimos de diez a una.", "De nada. ¿Necesita algo más?",
        "De nada, ¿necesita algo más?", "De res. Necessita alguna cosa més?"]
    assert "".join(heard) == "".join(answers)  # what was said is what is kept


def test_the_phrase_is_changed_as_the_words_go_by_however_they_are_cut():
    from vetdesk.agent.agent import Later

    text = "De nada. ¿En qué puedo ayudarle? Y ¿en qué quedamos?"
    wanted = "De nada. ¿Necesita algo más? Y ¿en qué quedamos?"
    for size in (1, 2, 3, 5, 8, 13, 60):
        later = Later("¿En qué puedo ayudarle?", "¿Necesita algo más?")
        pieces = [text[i:i + size] for i in range(0, len(text), size)]
        assert "".join(later.said(piece) for piece in pieces) + later.rest() == wanted, size
    # A piece that ends like the phrase begins is held, and let go when it was not it.
    later = Later("¿En qué puedo ayudarle?", "¿Necesita algo más?")
    assert later.said("Abrimos a las diez. ¿En qué") == "Abrimos a las diez. "
    assert later.said(" día viene?") == "¿En qué día viene?" and later.rest() == ""


def test_an_appointment_is_heard_when_the_agent_has_said_its_day_and_time(clinic, kb):
    """Heard on a call: "¿qué días le van bien para cambiar la cita de Kiko?", and the
    appointment was moved without the caller hearing which one it was. Being handed to
    the model is not being said."""
    said = "martes 10 de noviembre a las cinco de la tarde"
    model = ScriptedClient([Reply("¿Qué días le van bien para cambiar la cita de Kiko?"),
                            Reply(f"Tiene una cita para Kiko el {said}. ¿Es esa?"),
                            Reply(f"Tiene una cita para Kiko el {said}. ¿Es esa?")])
    call = _call(model, clinic, kb)
    toolbox = call._toolbox
    own = toolbox.agenda.book(datetime(2026, 11, 10, 17, 0), "revisión", "Kiko", client_code=10)
    toolbox.session.told[own.appointment_id] = 0
    call.say("Quiero cambiar la cita.")
    assert toolbox.session.heard == {}
    call.say("¿Cuál tengo?")
    assert toolbox.session.heard == {own.appointment_id: 2}
    # An answer taken back was not heard: the line it answered came again.
    assert call.take_back() and toolbox.session.heard == {}
    call.say("¿Cuál tengo, por favor?")
    assert toolbox.session.heard == {own.appointment_id: 3}
