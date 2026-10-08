"""The agent's tools: what they do, and above all what they refuse to do.

No model is involved. These tests call the tools the way a model would, including the
ways a careless or manipulated model might, and check the privacy barrier holds in code.
"""

import json
import re
from datetime import datetime

import pytest

from vetdesk.agent import SPECS, Toolbox
from vetdesk.evals.identity import probes_from_scenarios
from vetdesk.kb import load_kb
from vetdesk.legacy.normalize import fold
from vetdesk.llm import ToolCall
from vetdesk.scheduling import Appointment, SqliteAgenda

NOW = datetime(2026, 11, 3, 10, 15)
SLOT = "2026-11-09T09:30"
NOTHING = {"name": None, "name_spelled": False, "pet_name": None, "town": None}


@pytest.fixture(scope="module")
def kb():
    return load_kb()


def _toolbox(clinic, kb, number=None, reason="Le toca la vacuna y una revisión: es la primera.",
             accepted=(SLOT,)):
    """A toolbox on a call where the caller has already said what the visit is for, and
    has been offered SLOT and taken it."""
    toolbox = Toolbox(clinic, kb, SqliteAgenda(kb, lambda: NOW), lambda: NOW, number)
    if reason:
        toolbox.heard(reason)
    return _accepted(toolbox, *accepted)


def _accepted(toolbox, *starts):
    """The caller has heard these times offered, and has spoken since."""
    toolbox.session.offered.update(dict.fromkeys(starts, -1))
    return toolbox


def _call(toolbox, tool, /, **arguments):
    result = toolbox.run(ToolCall("call-1", tool, arguments))
    return json.loads(result.content), result.is_error


def _identify(toolbox, **given):
    for said in ("name", "pet_name", "town"):  # evidence counts only in the caller's words
        if given.get(said):
            toolbox.heard(given[said])
    return _call(toolbox, "identify_client", **{**NOTHING, **given})[0]


def _spell(toolbox, name):
    """The caller spells `name` aloud, which is what lets the model vouch for it."""
    toolbox.heard(" ".join("-".join(word.upper()) for word in name.split()))


def _by_phone(scenarios):
    """A caller the resolver confirms from the number and a clearly heard name."""
    return next(
        s for s in scenarios
        if s.category == "identity.phone_and_name" and s.speech.noise == "none"
        and s.identity_trace[-1].expect.decision == "resolved"
    )


@pytest.fixture
def confirmed(clinic, kb, scenarios, client_ids):
    """A toolbox mid-call, with the caller already confirmed."""
    for scenario in scenarios:
        if scenario.category != "identity.phone_and_name" or scenario.speech.noise != "none":
            continue
        toolbox = _toolbox(clinic, kb, scenario.call.caller_number)
        if _identify(toolbox, name=scenario.caller.says_name)["status"] == "confirmed":
            assert client_ids[toolbox.session.client.code] == scenario.caller.client_id
            return toolbox, scenario
    raise AssertionError("no scenario confirms by phone and name")


# --- the tool set ----------------------------------------------------------------------------


def test_tool_schemas_are_strict():
    for spec in SPECS:
        schema = spec.parameters
        assert schema["additionalProperties"] is False, spec.name
        assert schema["required"] == list(schema["properties"]), spec.name


def test_there_is_no_tool_to_transfer_a_call():
    names = {spec.name for spec in SPECS}
    assert names == {
        "identify_client", "get_pets", "get_availability", "book_appointment",
        "list_appointments", "cancel_appointment", "reschedule_appointment", "take_message",
    }
    assert not any("transfer" in spec.name or "handoff" in spec.name for spec in SPECS)


# --- the privacy barrier -----------------------------------------------------------------------


@pytest.mark.parametrize("tool,arguments", [
    ("get_pets", {}),
    ("list_appointments", {}),
    ("cancel_appointment", {"appointment_id": "AP-0001"}),
    ("reschedule_appointment", {"appointment_id": "AP-0001", "new_start": SLOT}),
])
def test_client_data_is_closed_until_the_caller_is_confirmed(clinic, kb, scenarios, tool,
                                                             arguments):
    scenario = _by_phone(scenarios)
    toolbox = _toolbox(clinic, kb, scenario.call.caller_number)  # the number IS on file
    result, failed = _call(toolbox, tool, **arguments)
    assert failed and set(result) == {"error"}
    assert "not confirmed" in result["error"]


def test_a_known_number_alone_reveals_nothing(clinic, kb, scenarios):
    scenario = _by_phone(scenarios)
    toolbox = _toolbox(clinic, kb, scenario.call.caller_number)
    result = _identify(toolbox)
    assert result == {"status": "need_more", "ask_for": "client_name",
                      "instructions": "Ask for their first name and both surnames."}
    assert toolbox.session.client is None


def test_nothing_about_any_client_leaks_before_confirmation(world, clinic, kb, scenarios):
    """Walk every identity scenario through the tool; until it says confirmed, its answers
    must not contain a single name from the clinic's records."""
    surnames = {fold(c.surname1) for c in world.clients.values()}
    surnames |= {fold(c.surname2) for c in world.clients.values()}
    pet_names = {fold(p.name) for p in world.pets.values()}
    checked = 0
    for probe in probes_from_scenarios(scenarios):
        toolbox = _toolbox(clinic, kb, probe.number)
        steps = [{"name": probe.name_heard}, {"pet_name": probe.pet_heard},
                 {"town": probe.town_heard}]
        for step in steps:
            if not any(step.values()):
                continue
            result = _identify(toolbox, **step)
            if result["status"] == "confirmed":
                break
            words = set(re.findall(r"[a-z]+", fold(json.dumps(result))))
            assert not words & (surnames | pet_names), (probe.id, result)
            checked += 1
    assert checked > 60


def test_identification_follows_the_resolver(clinic, kb, scenarios, client_ids):
    scenario = next(s for s in scenarios if s.category == "identity.hidden_number"
                    and s.speech.noise == "none")
    toolbox = _toolbox(clinic, kb)
    assert _identify(toolbox, name=scenario.caller.says_name)["ask_for"] == "pet_name"
    assert _identify(toolbox, pet_name=scenario.caller.pets[0].name)["ask_for"] == "town"
    result = _identify(toolbox, town=scenario.caller.town)
    assert result["status"] == "confirmed"
    assert client_ids[toolbox.session.client.code] == scenario.caller.client_id
    assert fold(scenario.caller.surname1) in fold(result["client_name"])


def test_someone_who_is_not_a_client(clinic, kb):
    toolbox = _toolbox(clinic, kb)
    result = _identify(toolbox, name="Nadie Conocido Aquí")
    assert result["status"] == "not_a_client"
    assert _call(toolbox, "get_pets")[1]


def test_a_spelled_name_stays_spelled(clinic, kb):
    toolbox = _toolbox(clinic, kb)
    _spell(toolbox, "Nadie Conocido Aquí")
    _identify(toolbox, name="Nadie Conocido Aquí", name_spelled=True)
    _identify(toolbox, name="Nadie Conocido Aquí", pet_name="Luna")
    assert toolbox.session.evidence.name_verified
    assert toolbox.session.evidence.pet_name == "Luna"


def test_once_confirmed_the_caller_stays_who_they_are(confirmed):
    toolbox, _ = confirmed
    client = toolbox.session.client
    assert _identify(toolbox, name="Otra Persona Distinta")["status"] == "confirmed"
    assert toolbox.session.client is client


def test_the_model_cannot_vouch_for_a_name_the_caller_did_not_spell(clinic, kb, scenarios):
    """Found by the evaluation harness: the caller spelled R-O-S-S-E-L-L-Ó and the model
    passed "Rossellón" as spelled. The flag switches off the resolver's doubt about a
    misheard name, so the tool checks it against the caller's own words."""
    scenario = next(
        s for s in scenarios
        if s.category == "identity.phone_and_name" and s.speech.noise == "none"
        and _identify(_toolbox(clinic, kb, s.call.caller_number),
                      name=s.caller.says_name)["status"] == "confirmed"
    )
    name = scenario.caller.says_name
    toolbox = _toolbox(clinic, kb, scenario.call.caller_number)

    refused, failed = _call(toolbox, "identify_client", **{**NOTHING, "name": name,
                                                           "name_spelled": True})
    assert failed and "They have spelled nothing" in refused["error"]
    assert toolbox.session.evidence.client_name is None  # nothing reached the resolver

    _spell(toolbox, name)
    wrong = name + "n"  # the letters put back together wrong
    refused, failed = _call(toolbox, "identify_client", **{**NOTHING, "name": wrong,
                                                           "name_spelled": True})
    assert failed and "They spelled: " + ", ".join(w.upper() for w in name.split()) \
        in refused["error"]
    assert toolbox.session.client is None

    assert _identify(toolbox, name=name, name_spelled=True)["status"] == "confirmed"
    # Accents and case are not what spelling is about.
    other = _toolbox(clinic, kb)
    other.heard("Sí: m-u-ñ-o-z, G-O-N-Z-A-L-E-Z.")
    assert "error" not in _identify(other, name="Muñoz González", name_spelled=True)


def test_a_pets_name_counts_as_confirmed_only_if_repeated_or_spelled(clinic, kb):
    """The tool reads it off the caller's words: there is no flag for the model to set."""
    def passed(toolbox, pet):
        assert not _call(toolbox, "identify_client", **{**NOTHING, "pet_name": pet})[1]
        return toolbox.session.evidence.pet_verified

    toolbox = _toolbox(clinic, kb)
    toolbox.heard("Es para Yuna.")
    assert not passed(toolbox, "Yuna")
    toolbox.heard("Sí, Yuna.")
    assert passed(toolbox, "Yuna")
    spelled = _toolbox(clinic, kb)
    spelled.heard("Para Lluna: L-L-U-N-A")
    assert passed(spelled, "Lluna")
    assert "pet_confirmed" not in SPECS[0].parameters["properties"]


def test_what_a_model_passes_is_read_generously(clinic, kb, scenarios):
    """The word "null" for nothing, and a spelling handed over letter by letter."""
    scenario = _by_phone(scenarios)
    toolbox = _toolbox(clinic, kb, scenario.call.caller_number)
    result, failed = _call(toolbox, "identify_client",
                           **{**NOTHING, "name": "null", "town": "None", "pet_name": ""})
    assert not failed and result["ask_for"] == "client_name"
    name = scenario.caller.says_name
    letters = " ".join("-".join(word.upper()) for word in name.split())
    toolbox.heard(letters)
    assert not _call(toolbox, "identify_client", **{**NOTHING, "name": letters})[1]
    evidence = toolbox.session.evidence
    assert fold(evidence.client_name) == fold(name) and evidence.name_verified


def test_a_town_the_caller_never_said_is_not_evidence(clinic, kb, scenarios):
    scenario = next(s for s in scenarios if s.category == "identity.hidden_number"
                    and s.speech.noise == "none")
    toolbox = _toolbox(clinic, kb)
    _identify(toolbox, name=scenario.caller.says_name)
    assert _identify(toolbox, pet_name=scenario.caller.pets[0].name)["ask_for"] == "town"
    # The model fills the town in by itself: the right one, as it happens.
    guess = {**NOTHING, "town": scenario.caller.town}
    refused, failed = _call(toolbox, "identify_client", **guess)
    assert failed and "has not said that town" in refused["error"]
    assert toolbox.session.client is None and toolbox.session.evidence.town is None
    toolbox.heard(f"Visc a {scenario.caller.town.upper()}, al centre.")
    confirmed, failed = _call(toolbox, "identify_client", **guess)
    assert not failed and confirmed["status"] == "confirmed"


def test_a_name_the_model_corrected_is_not_what_the_caller_said(clinic, kb):
    toolbox = _toolbox(clinic, kb)
    toolbox.heard("Me llamo Yoaquín Ortega Roca, llamo por mi gata Yuna.")
    fixed, failed = _call(toolbox, "identify_client", **{**NOTHING, "name": "Joaquín Ortega Roca"})
    assert failed and "joaquin was never said" in fixed["error"]
    fixed, failed = _call(toolbox, "identify_client", **{**NOTHING, "pet_name": "Lluna"})
    assert failed and "lluna was never said" in fixed["error"]
    assert toolbox.session.evidence.client_name is None
    as_heard = {**NOTHING, "name": "Yoaquín Ortega Roca", "pet_name": "Yuna"}
    assert not _call(toolbox, "identify_client", **as_heard)[1]
    # Spelled out, the words need not have been said whole.
    toolbox.heard("J-O-A-Q-U-Í-N")
    assert not _call(toolbox, "identify_client", **{**NOTHING, "name": "Joaquín Ortega Roca"})[1]


def test_a_field_left_out_means_not_said(clinic, kb, scenarios):
    scenario = _by_phone(scenarios)
    toolbox = _toolbox(clinic, kb, scenario.call.caller_number)
    toolbox.heard(f"Soy {scenario.caller.says_name}.")
    result, failed = _call(toolbox, "identify_client", name=scenario.caller.says_name)
    assert not failed and "status" in result
    evidence = toolbox.session.evidence
    assert evidence.client_name == scenario.caller.says_name and not evidence.name_verified
    assert evidence.town is None and not evidence.pet_verified


# --- what a confirmed caller can do ---------------------------------------------------------------


def test_pets_of_a_confirmed_caller(confirmed):
    toolbox, scenario = confirmed
    result, failed = _call(toolbox, "get_pets")
    assert not failed
    assert {pet.name for pet in scenario.caller.pets} <= {pet["name"] for pet in result["pets"]}


def test_animals_that_may_be_a_namesakes_are_not_handed_to_the_model(clinic, kb, scenarios):
    """Found by the evaluation harness: `get_pets` listed them with a note not to mention
    them. A barrier that depends on the model heeding a note is not a barrier."""
    checked = 0
    for scenario in scenarios:
        if scenario.category != "identity.homonym_with_phone":
            continue
        toolbox = _toolbox(clinic, kb, scenario.call.caller_number)
        name = " ".join(p for p in (scenario.caller.given_name, scenario.caller.surname1,
                                    scenario.caller.surname2) if p)
        _spell(toolbox, name)
        if _identify(toolbox, name=name, name_spelled=True)["status"] != "confirmed":
            continue
        animals = clinic.animals_of(toolbox.session.client.code)
        shared = {a.name for a in animals if len(a.owner_codes) > 1}
        if not shared:
            continue
        checked += 1
        result, failed = _call(toolbox, "get_pets")
        assert not failed and all("certain" not in pet for pet in result["pets"])
        assert {pet["name"] for pet in result["pets"]} == \
            {a.name for a in animals if len(a.owner_codes) == 1}
        text = json.dumps(result, ensure_ascii=False)
        only_shared = shared - {a.name for a in animals if len(a.owner_codes) == 1}
        assert not any(name in text for name in only_shared)
        hidden = sum(len(a.owner_codes) > 1 for a in animals)
        assert f"{hidden} more on file" in result["note"]
        # The caller can still book for one of them by naming it.
        booked, failed = _call(toolbox, "book_appointment", start=SLOT, reason="vacuna",
                               pet_name=sorted(shared)[0], contact_name=None,
                               contact_phone=None)
        assert not failed and booked["pet_name"] == sorted(shared)[0]
    assert checked


def test_booking_for_a_confirmed_caller_goes_on_their_record(confirmed):
    toolbox, scenario = confirmed
    pet = scenario.caller.pets[0].name
    result, failed = _call(toolbox, "book_appointment", start=SLOT, reason="vacuna",
                           pet_name=pet.upper(), contact_name=None, contact_phone=None)
    assert not failed and result["status"] == "booked"
    assert result["say"] == "lunes 9 de noviembre a las nueve y media de la mañana"
    booked = toolbox.agenda.get(result["appointment_id"])
    assert booked.verified and booked.client_code == toolbox.session.client.code
    assert booked.pet_name == pet and booked.animal_code is not None
    assert "reception will confirm" not in result["note"]


def test_a_new_pet_of_a_confirmed_caller(confirmed):
    toolbox, _ = confirmed
    result, _ = _call(toolbox, "book_appointment", start=SLOT, reason="primera visita",
                      pet_name="Zarpas", contact_name=None, contact_phone=None)
    booked = toolbox.agenda.get(result["appointment_id"])
    assert (booked.pet_name, booked.animal_code, booked.verified) == ("Zarpas", None, True)


def test_cancelling_and_moving_own_appointments(confirmed):
    toolbox, scenario = confirmed
    own = toolbox.agenda.book(datetime(2026, 11, 10, 17, 0), "revisión",
                              scenario.caller.pets[0].name,
                              client_code=toolbox.session.client.code)
    # Not before the caller has been told which appointment it is: heard on a real call,
    # "quería anular una cita", a name, and it was cancelled in the same breath.
    for tool, more in (("cancel_appointment", {}),
                       ("reschedule_appointment", {"new_start": "2026-11-11T11:00"})):
        refused, failed = _call(toolbox, tool, appointment_id=own.appointment_id, **more)
        assert failed and "First tell the caller which appointment" in refused["error"]
    listed, _ = _call(toolbox, "list_appointments")
    assert [a["appointment_id"] for a in listed["appointments"]] == [own.appointment_id]
    refused, failed = _call(toolbox, "cancel_appointment", appointment_id=own.appointment_id)
    assert failed and "once they have said yes" in refused["error"]  # told, not yet answered
    assert toolbox.agenda.get(own.appointment_id).status == "booked"
    _accepted(toolbox, "2026-11-11T11:00").heard("Sí, esa. El miércoles a las once me va bien.")
    moved, failed = _call(toolbox, "reschedule_appointment",
                          appointment_id=own.appointment_id, new_start="2026-11-11T11:00")
    assert not failed and moved["start"] == "2026-11-11T11:00"
    cancelled, failed = _call(toolbox, "cancel_appointment", appointment_id=own.appointment_id)
    assert not failed and cancelled["status"] == "cancelled"
    assert _call(toolbox, "list_appointments")[0] == {"appointments": []}
    assert "cannot be cancelled or moved" not in _identify(toolbox)["instructions"]  # own phone


def test_cancelling_and_moving_need_a_call_from_a_phone_on_the_record(clinic, kb, scenarios):
    """Name, pet and town are things a friend knows: enough to be served, not to undo."""
    scenario = next(s for s in scenarios if s.category == "identity.hidden_number"
                    and s.speech.noise == "none")
    toolbox = _toolbox(clinic, kb)  # a hidden number
    _identify(toolbox, name=scenario.caller.says_name)
    _identify(toolbox, pet_name=scenario.caller.pets[0].name)
    confirmed = _identify(toolbox, town=scenario.caller.town)
    assert confirmed["status"] == "confirmed"
    assert "cannot be cancelled or moved" in confirmed["instructions"]
    own = toolbox.agenda.book(datetime(2026, 11, 10, 17, 0), "revisión",
                              scenario.caller.pets[0].name,
                              client_code=toolbox.session.client.code)
    listed, _ = _call(toolbox, "list_appointments")
    assert [a["appointment_id"] for a in listed["appointments"]] == [own.appointment_id]
    refused, failed = _call(toolbox, "cancel_appointment", appointment_id=own.appointment_id)
    assert failed and "take_message" in refused["error"]
    # The same answer for an appointment that is not theirs or does not exist.
    assert _call(toolbox, "cancel_appointment", appointment_id="AP-0999")[0] == refused
    assert _call(toolbox, "reschedule_appointment", appointment_id=own.appointment_id,
                 new_start="2026-11-11T11:00") == (refused, True)
    kept = toolbox.agenda.get(own.appointment_id)
    assert kept.status == "booked" and kept.start == datetime(2026, 11, 10, 17, 0)


def test_somebody_elses_appointment_cannot_be_touched_or_detected(confirmed):
    toolbox, _ = confirmed
    other = toolbox.session.client.code + 10_000
    toolbox.agenda.add(Appointment("AP-0500", datetime(2026, 11, 10, 17, 0), "revisión",
                                   "Toby", other, None, None, None, True))
    theirs, failed_theirs = _call(toolbox, "cancel_appointment", appointment_id="AP-0500")
    missing, failed_missing = _call(toolbox, "cancel_appointment", appointment_id="AP-0999")
    assert failed_theirs and failed_missing
    assert theirs == missing  # no way to tell an existing appointment from none
    assert _call(toolbox, "reschedule_appointment", appointment_id="AP-0500",
                 new_start=SLOT)[1]
    assert toolbox.agenda.get("AP-0500").status == "booked"
    assert toolbox.agenda.get("AP-0500").start == datetime(2026, 11, 10, 17, 0)


# --- what anybody can do -------------------------------------------------------------------------


def test_availability_needs_no_identification(clinic, kb):
    toolbox = _toolbox(clinic, kb)
    result, failed = _call(toolbox, "get_availability", date_from="2026-11-09",
                           date_to="2026-11-13", part_of_day="afternoon")
    assert not failed and len(result["slots"]) == 6
    assert result["slots"][0] == {
        "start": "2026-11-09T16:30",
        "say": "lunes 9 de noviembre a las cuatro y media de la tarde",
    }
    # One way of saying each time, in the language the call is going on in.
    for language, words in (("ca", "dilluns 9 de novembre a les quatre i mitja de la tarda"),
                            ("en", "Monday 9 November at four thirty in the afternoon")):
        toolbox.session.language = language
        result, _ = _call(toolbox, "get_availability", date_from="2026-11-09",
                          date_to="2026-11-13", part_of_day="afternoon")
        assert result["slots"][0] == {"start": "2026-11-09T16:30", "say": words}
    toolbox.session.language = "es"
    assert _call(toolbox, "get_availability", date_from="2026-11-08", date_to="2026-11-08",
                 part_of_day="any")[0]["slots"] == []


def test_dates_in_the_past_are_refused_not_answered_with_nothing_free(clinic, kb):
    """A model that gets the year wrong must be told so, not that the week is full."""
    toolbox = _toolbox(clinic, kb)
    refused, failed = _call(toolbox, "get_availability", date_from="2025-11-09",
                            date_to="2025-11-13", part_of_day="afternoon")
    assert failed and "in the past" in refused["error"] and "2026-11-03" in refused["error"]
    # A range that starts before today and reaches it is still worth answering.
    assert not _call(toolbox, "get_availability", date_from="2026-11-01",
                     date_to="2026-11-06", part_of_day="any")[1]


def test_free_times_are_a_sample_over_several_days_and_say_so(clinic, kb):
    """Found by the evaluation harness: given the six earliest times, all on a Monday, the
    model told callers there was nothing else that week."""
    toolbox = _toolbox(clinic, kb)
    week, _ = _call(toolbox, "get_availability", date_from="2026-11-09", date_to="2026-11-13",
                    part_of_day="any")
    assert [slot["say"].split()[0] for slot in week["slots"]] == \
        ["lunes", "lunes", "martes", "martes", "miércoles", "miércoles"]
    assert len({slot["start"] for slot in week["slots"]}) == 6
    assert week["free_days"] == ["lunes 2026-11-09", "martes 2026-11-10", "miércoles 2026-11-11",
                                 "jueves 2026-11-12", "viernes 2026-11-13"]
    assert "never say a day or the week is full" in week["note"]

    day, _ = _call(toolbox, "get_availability", date_from="2026-11-09", date_to="2026-11-09",
                   part_of_day="morning")
    assert len(day["slots"]) == 6 and day["free_days"] == ["lunes 2026-11-09"]
    assert day["slots"][0]["start"] == "2026-11-09T09:30"

    # When everything fits, everything is shown and the model may say so.
    for start in ("10:00", "10:30", "11:00", "11:30"):
        toolbox.agenda.book(datetime.fromisoformat(f"2026-11-14T{start}"), "x", "Luna",
                            contact_name="A", contact_phone="+34600000001")
    saturday, _ = _call(toolbox, "get_availability", date_from="2026-11-14",
                        date_to="2026-11-14", part_of_day="any")
    assert [slot["start"][-5:] for slot in saturday["slots"]] == ["12:00", "12:30"]
    assert saturday["note"] == "These are all the free times in that range."
    assert "free_days" not in saturday


def test_an_unconfirmed_caller_books_under_their_word(clinic, kb):
    toolbox = _toolbox(clinic, kb)
    booking = {"start": SLOT, "reason": "vacuna", "pet_name": "Luna"}
    refused, failed = _call(toolbox, "book_appointment", **booking, contact_name=None,
                            contact_phone=None)
    assert failed and "contact_name and contact_phone are required" in refused["error"]
    refused, failed = _call(toolbox, "book_appointment", **booking,
                            contact_name="Lucía Romero", contact_phone="no lo sé")
    assert failed and "not a valid phone number" in refused["error"]
    result, failed = _call(toolbox, "book_appointment", **booking,
                           contact_name="Lucía Romero", contact_phone="600 11 22 33")
    assert not failed and "reception will confirm" in result["note"]
    booked = toolbox.agenda.get(result["appointment_id"])
    assert (booked.client_code, booked.verified) == (None, False)
    assert booked.contact_phone == "+34600112233"


def test_a_taken_time_is_refused_with_a_way_forward(clinic, kb):
    toolbox = _toolbox(clinic, kb)
    booking = {"start": SLOT, "reason": "vacuna", "pet_name": "Luna",
               "contact_name": "Lucía Romero", "contact_phone": "600112233"}
    assert not _call(toolbox, "book_appointment", **booking)[1]
    refused, failed = _call(toolbox, "book_appointment", **{**booking, "pet_name": "Bruno"})
    assert failed and "get_availability" in refused["error"]


def test_messages_for_reception(clinic, kb):
    hidden = _toolbox(clinic, kb)
    refused, failed = _call(hidden, "take_message", message="Quiere hablar de una factura",
                            contact_name="Lucía Romero", contact_phone=None)
    assert failed and "no number to call back" in refused["error"]
    taken, failed = _call(hidden, "take_message", message="Quiere hablar de una factura",
                          contact_name="Lucía Romero", contact_phone="600112233")
    assert not failed and "do not say you are transferring" in taken["instructions"]
    assert hidden.session.messages[0].contact_phone == "+34600112233"

    # With a number on the line and none given, it is the one the call comes from. But
    # the caller is not confirmed and has not said to use it: they are asked first.
    known = _toolbox(clinic, kb, "+34600999888")
    message = {"message": "Que la llamen", "contact_name": "Ana", "contact_phone": None}
    refused, failed = _call(known, "take_message", **message)
    assert failed and "has not said to use it" in refused["error"] and not known.session.messages
    known.heard("Sí, a este.")
    _call(known, "take_message", **message)
    assert known.session.messages[0].contact_phone == "+34600999888"


def test_the_calling_number_is_not_taken_for_the_callers_until_they_are_asked(clinic, kb):
    """Seen on the demo's page: an unverified appointment for a caller on another client's
    phone, with that client's number as the one to check it on. Nobody had said it."""
    number = "+34600999888"
    booking = {"start": "2026-11-09T16:30", "reason": "revisión", "pet_name": "Toby",
               "contact_name": "Marta Soler", "contact_phone": number}
    toolbox = _toolbox(clinic, kb, number, accepted=("2026-11-09T16:30",))
    toolbox.heard("Quería una revisión para mi perro Toby el lunes por la tarde.")
    for _ in range(2):  # asked again in the same breath, it is still not yet
        refused, failed = _call(toolbox, "book_appointment", **booking)
        assert failed and "Ask which phone number reception can reach them on" in refused["error"]
    assert toolbox.agenda.all() == []
    toolbox.heard("Sí, apúntelo.")
    booked, failed = _call(toolbox, "book_appointment", **booking)
    assert not failed and toolbox.agenda.all()[0].contact_phone == number

    # Another number, in their own words, is taken at once; and so is this one when they
    # have said that it is the same.
    other = _toolbox(clinic, kb, number, accepted=("2026-11-09T16:30",))
    other.heard("Una revisión para Toby el lunes por la tarde. Mi teléfono es el 600 11 22 33.")
    assert not _call(other, "book_appointment", **{**booking, "contact_phone": "600112233"})[1]
    same = _toolbox(clinic, kb, number, accepted=("2026-11-09T16:30",))
    same.heard("Una revisión para Toby el lunes por la tarde, y me llaman a este mismo número.")
    assert not _call(same, "book_appointment", **booking)[1]


def test_the_clinics_own_number_is_not_where_to_reach_a_caller(clinic, kb):
    """A model with no number for the caller fills in one it does know: the clinic's."""
    toolbox = _toolbox(clinic, kb)  # a hidden number
    for own in (kb.clinic.phone, "971 555 010", kb.emergency.phone):
        refused, failed = _call(toolbox, "take_message", message="Que la llamen",
                                contact_name="Ana", contact_phone=own)
        assert failed and "clinic's own numbers" in refused["error"], own
        refused, failed = _call(toolbox, "book_appointment", start=SLOT, reason="revisión",
                                pet_name="Toby", contact_name="Ana", contact_phone=own)
        assert failed and "clinic's own numbers" in refused["error"], own
    assert toolbox.session.messages == []
    assert all(event.is_error for event in toolbox.session.events)



# --- the day and the time are the caller's -------------------------------------------------------


def test_a_time_the_caller_has_not_heard_and_accepted_is_not_booked(clinic, kb):
    """Heard on a call and counted in the calls played before it: an appointment put at a
    time the caller had not said a word about."""
    toolbox = _toolbox(clinic, kb, accepted=())
    booking = {"reason": "vacuna", "pet_name": "Luna", "contact_name": "Lucía Romero",
               "contact_phone": "600112233"}
    week, _ = _call(toolbox, "get_availability", date_from="2026-11-09", date_to="2026-11-13",
                    part_of_day="any")
    offered = week["slots"][0]
    for _ in range(2):  # looked up and booked in the same breath: the caller heard nothing
        refused, failed = _call(toolbox, "book_appointment", start=offered["start"], **booking)
        assert failed and "has not been offered this time" in refused["error"]
        assert offered["say"] in refused["error"]
    assert toolbox.agenda.all() == []
    toolbox.heard("Sí, esa me va bien.")
    assert not _call(toolbox, "book_appointment", start=offered["start"], **booking)[1]


def test_a_time_the_caller_named_is_said_back_before_it_is_booked(clinic, kb):
    """Taking a time for the caller's because a word of theirs was in it let through what
    it was meant to stop: "esta mañana" named every morning, "buenas tardes" every
    afternoon. So even a time named outright is said back, and booked on their yes."""
    toolbox = _toolbox(clinic, kb, accepted=())
    booking = {"start": "2026-11-10T18:00", "reason": "vacuna", "pet_name": "Luna",
               "contact_name": "Lucía Romero", "contact_phone": "600112233"}
    toolbox.heard("Buenas tardes. El martes a las seis de la tarde, si puede ser.")
    refused, failed = _call(toolbox, "book_appointment", **booking)
    assert failed and "say it back and ask whether it is right" in refused["error"]
    assert "martes 10 de noviembre a las seis de la tarde" in refused["error"]
    toolbox.heard("Sí, eso es.")
    assert not _call(toolbox, "book_appointment", **booking)[1]


def test_an_appointment_is_not_moved_to_a_time_of_the_models_own(confirmed):
    """ "Tengo que cambiar la cita", and it was moved to the next morning without a word."""
    toolbox, scenario = confirmed
    own = toolbox.agenda.book(datetime(2026, 11, 10, 17, 0), "revisión",
                              scenario.caller.pets[0].name,
                              client_code=toolbox.session.client.code)
    _call(toolbox, "list_appointments")
    toolbox.heard("Sí, esa. Me he dado cuenta esta mañana: tengo que cambiarla.")
    week, _ = _call(toolbox, "get_availability", date_from="2026-11-11", date_to="2026-11-13",
                    part_of_day="any")
    for new in (week["slots"][0]["start"], "2026-11-12T17:00"):
        refused, failed = _call(toolbox, "reschedule_appointment",
                                appointment_id=own.appointment_id, new_start=new)
        assert failed and "has not been offered this time" in refused["error"], new
    assert toolbox.agenda.get(own.appointment_id).start == datetime(2026, 11, 10, 17, 0)
    toolbox.heard("El jueves a las cinco.")
    moved, failed = _call(toolbox, "reschedule_appointment",
                          appointment_id=own.appointment_id, new_start="2026-11-12T17:00")
    assert not failed and moved["start"] == "2026-11-12T17:00"


@pytest.mark.parametrize("said", [
    "Sí, esa misma, y quería cambiarla.", "Sí, aquesta, però la voldria canviar de dia.",
    "Yes, that one. I'd like to move it.", "Ja, genau, ich möchte ihn verschieben.",
    "Oui, celui-là, je voudrais le déplacer.", "Sì, quello, vorrei spostarlo.",
    "Да, эту, я хочу её перенести."])
def test_an_appointment_the_caller_wants_moved_is_not_cancelled(confirmed, said):
    """ "No voy a poder ir", "¿es esa la que quiere cancelar?", "sí, esa misma, y quería
    cambiarla": and it was cancelled, and booked again at another time."""
    toolbox, scenario = confirmed
    own = toolbox.agenda.book(datetime(2026, 11, 10, 17, 0), "revisión",
                              scenario.caller.pets[0].name,
                              client_code=toolbox.session.client.code)
    _call(toolbox, "list_appointments")
    toolbox.heard(said)
    refused, failed = _call(toolbox, "cancel_appointment", appointment_id=own.appointment_id)
    assert failed and "they want it changed, not cancelled" in refused["error"]
    assert toolbox.agenda.get(own.appointment_id).status == "booked"
    toolbox.heard("No, mejor anúlela. Adelante, y me llaman al móvil si hace falta.")
    assert not _call(toolbox, "cancel_appointment", appointment_id=own.appointment_id)[1]


# --- an appointment made on this call ------------------------------------------------------------


def test_whoever_booked_on_this_call_can_have_it_moved_or_cancelled(clinic, kb):
    """Heard on a call: booked for the 13th, "el 13 no puedo, una semana más tarde", and a
    second appointment booked for the 20th. Nobody who is not confirmed could have one
    moved, so the model could only book again."""
    later = "2026-11-16T09:30"
    toolbox = _toolbox(clinic, kb)
    booking = {"reason": "vacuna", "pet_name": "Nichi", "contact_name": "Marcelo Mastriani",
               "contact_phone": "600112233"}
    booked, _ = _call(toolbox, "book_appointment", start=SLOT, **booking)
    assert "move this appointment with reschedule_appointment" in booked["note"]
    ours = booked["appointment_id"]
    # Not in the same breath: the caller has not heard yet what was booked.
    assert _call(toolbox, "cancel_appointment", appointment_id=ours)[1]
    toolbox.heard("Ay, el 9 no puedo. Tendría que ser una semana más tarde.")
    # Booking it again is refused, with the way to do it; and the new time is said first.
    again, failed = _call(toolbox, "book_appointment", start=later, **booking)
    assert failed and "reschedule_appointment" in again["error"] and ours in again["error"]
    refused, failed = _call(toolbox, "reschedule_appointment", appointment_id=ours,
                            new_start=later)
    assert failed and "lunes 16 de noviembre a las nueve y media" in refused["error"]
    toolbox.heard("Sí, ese día sí.")
    moved, failed = _call(toolbox, "reschedule_appointment", appointment_id=ours,
                          new_start=later)
    assert not failed and moved["status"] == "rescheduled"
    assert [(a.start, a.status) for a in toolbox.agenda.all()] == \
        [(datetime(2026, 11, 16, 9, 30), "booked")]
    assert [notice.kind for notice in toolbox.session.notices] == ["booked", "moved"]
    assert "SIN VERIFICAR: dice ser Marcelo Mastriani" in toolbox.session.notices[-1].text
    # Another animal is another visit.
    assert not _call(toolbox, "book_appointment", start=SLOT, **{**booking, "pet_name": "Rex"})[1]
    toolbox.heard("No, déjelo, anule la de Nichi.")
    gone, failed = _call(toolbox, "cancel_appointment", appointment_id=ours)
    assert not failed and gone["status"] == "cancelled"
    # Cancelled, it is no longer theirs to touch, and the animal can be booked afresh.
    assert _call(toolbox, "cancel_appointment", appointment_id=ours)[1]
    assert not _call(toolbox, "book_appointment", start=later, **booking)[1]


def test_only_what_was_booked_on_this_call_is_open_to_a_caller_not_confirmed(clinic, kb):
    toolbox = _toolbox(clinic, kb)
    toolbox.agenda.add(Appointment("AP-0500", datetime(2026, 11, 10, 17, 0), "revisión",
                                   "Toby", 10, None, None, None, True))
    toolbox.agenda.book(datetime(2026, 11, 11, 17, 0), "revisión", "Rex",
                        contact_name="Otra Persona", contact_phone="+34600000009")
    toolbox.heard("Anule la cita del martes.")
    for appointment in toolbox.agenda.all():
        for tool, more in (("cancel_appointment", {}),
                           ("reschedule_appointment", {"new_start": SLOT})):
            refused, failed = _call(toolbox, tool, appointment_id=appointment.appointment_id,
                                    **more)
            assert failed and "The caller is not confirmed" in refused["error"]
    assert [a.status for a in toolbox.agenda.all()] == ["booked", "booked"]


# --- what cannot be done on this call -------------------------------------------------------------


def test_what_cannot_be_done_is_said_only_once_a_tool_has_found_so(clinic, kb, scenarios):
    """It was a set phrase for a day, and the model turned down with it callers it had not
    asked who they were, and a client on their own phone: 10 requests of 24."""
    from vetdesk.agent.prompt import NOT_FROM_HERE, PHRASES, call_context, system_prompt

    every = list(NOT_FROM_HERE.values())
    assert set(NOT_FROM_HERE) == set(PHRASES)
    assert not any(phrase in system_prompt(kb) + call_context(kb, NOW, None, "")
                   for phrase in every)
    assert not any(phrase in " ".join(phrases) for phrase in every for phrases in PHRASES.values())

    # Nobody has been asked who they are: the answer is to ask, not to turn them down.
    toolbox = _toolbox(clinic, kb)
    refused, failed = _call(toolbox, "list_appointments")
    assert failed and "Ask who is calling" in refused["error"]
    assert not any(phrase in refused["error"] for phrase in every)
    # Not a client: now it cannot be done, and the words for it are handed over, in the
    # language of the call.
    toolbox.session.language = "ca"
    nobody = _identify(toolbox, name="Marcelo Mastriani Roca")
    assert nobody["status"] == "not_a_client" and NOT_FROM_HERE["ca"] in nobody["instructions"]
    assert "anybody can book one" in nobody["instructions"]
    refused, _ = _call(toolbox, "list_appointments")
    assert NOT_FROM_HERE["ca"] in refused["error"] and "take_message" in refused["error"]

    # A client on the phone of their record is never given the words; from another phone,
    # for cancelling and moving only.
    scenario = _by_phone(scenarios)
    own = _toolbox(clinic, kb, scenario.call.caller_number)
    known = _identify(own, name=scenario.caller.says_name)
    assert known["status"] == "confirmed" and NOT_FROM_HERE["es"] not in known["instructions"]
    hidden = next(s for s in scenarios if s.category == "identity.hidden_number"
                  and s.speech.noise == "none")
    away = _toolbox(clinic, kb)
    _identify(away, name=hidden.caller.says_name)
    _identify(away, pet_name=hidden.caller.pets[0].name)
    known = _identify(away, town=hidden.caller.town)
    assert known["status"] == "confirmed" and NOT_FROM_HERE["es"] in known["instructions"]


# --- a model that gets things wrong ---------------------------------------------------------------


def test_bad_calls_are_answered_not_raised(clinic, kb):
    toolbox = _toolbox(clinic, kb)
    assert _call(toolbox, "transfer_call", to="reception")[1]
    assert _call(toolbox, "get_availability", date_from="mañana", date_to="2026-11-13",
                 part_of_day="any")[1]
    assert _call(toolbox, "get_availability", date_from="2026-11-13", date_to="2026-11-09",
                 part_of_day="any")[1]
    assert _call(toolbox, "book_appointment", start=SLOT)[1]  # missing arguments
    assert _call(toolbox, "identify_client", name="Ana", surname="Mas")[1]  # no such field
    assert [event.is_error for event in toolbox.session.events] == [True] * 5



def test_a_reason_the_caller_never_gave_is_not_booked(clinic, kb):
    """Heard on a call: asked what the visit was for, the caller corrected the kind of
    animal, and the booking went into the agenda with a reason of the model's own."""
    toolbox = _toolbox(clinic, kb, reason=None)
    for line in ("Vull agendar una cita.", "Pel meu gosset, Pep Toni.",
                 "No és un gosset, és un hàmster.", "El dimarts em va bé."):
        toolbox.heard(line)
    booking = {"start": SLOT, "pet_name": "Pep Toni", "contact_name": "Pere Garriga",
               "contact_phone": "973 664 209"}
    for invented in ("Revisió", "Visita general", "Revisió hàmster", "Cita per al Pep Toni"):
        refused, failed = _call(toolbox, "book_appointment", **booking, reason=invented)
        assert failed and "has not said what the visit is for" in refused["error"], invented
    toolbox.heard("Té una taca blava a l'esquena.")
    for theirs in ("Taca blava a l'esquena", "té una taca"):
        fresh = _toolbox(clinic, kb, reason="Té una taca blava a l'esquena.")
        assert not _call(fresh, "book_appointment", **booking, reason=theirs)[1], theirs


def test_the_callers_reason_may_be_put_in_other_words_of_the_same_root(clinic, kb):
    booking = {"start": SLOT, "pet_name": "Luna", "contact_name": "Lucía Romero",
               "contact_phone": "600 11 22 33"}
    for said, written in (("Hay que vacunarlo.", "Vacunación anual"), ("Cojea un poco.", "Cojera"),
                          ("Se rasca mucho.", "Se rasca"), ("Té tos.", "Tos"),
                          ("Una revisión.", "Revisión general")):
        toolbox = _toolbox(clinic, kb, reason=said)
        assert not _call(toolbox, "book_appointment", **booking, reason=written)[1], written


def test_the_callers_own_name_is_not_their_pets(clinic, kb):
    """Seen once: asked their name, a caller said "Carme Llull" and the model handed "Carme"
    over as the pet's name too. With a pet's name on the table nobody asked for the real
    one, and the caller ended the call unconfirmed."""
    toolbox = _toolbox(clinic, kb, reason=None)
    toolbox.heard("Carme Llull.")
    result, failed = _call(toolbox, "identify_client", name="Carme Llull", pet_name="Carme")
    assert not failed and toolbox.session.evidence.pet_name is None
    # A pet that does share the name is named again, in a line of its own.
    toolbox.heard("Es diu Carme, com jo.")
    _call(toolbox, "identify_client", name="Carme Llull", pet_name="Carme")
    assert toolbox.session.evidence.pet_name == "Carme"


def test_a_reason_given_in_another_alphabet_is_still_the_callers(clinic, kb):
    """The names on file are compared in Latin letters. A reason is not a name: a caller
    speaking Russian gives it in Russian, and it must not be refused for that."""
    booking = {"start": SLOT, "pet_name": "Rex", "contact_name": "Ivan Petrov",
               "contact_phone": "600 11 22 33"}
    toolbox = _toolbox(clinic, kb, reason=None)
    toolbox.heard("Я хотел бы записаться на приём для моей собаки.")
    refused, failed = _call(toolbox, "book_appointment", **booking, reason="Осмотр")
    assert failed and "has not said what the visit is for" in refused["error"]
    toolbox.heard("Ему нужна прививка.")
    assert not _call(toolbox, "book_appointment", **booking, reason="Прививка")[1]
    for said, written in (("Er hinkt seit gestern.", "Hinken"), ("Il boite.", "Boiterie"),
                          ("Deve fare il vaccino.", "Vaccino annuale")):
        toolbox = _toolbox(clinic, kb, reason=said)
        assert not _call(toolbox, "book_appointment", **booking, reason=written)[1], written



def test_a_day_asked_for_that_is_a_holiday_is_said_to_be_one(clinic, kb):
    """Asked for the Monday, the model was shown a day with nothing free and no reason."""
    now = datetime(2026, 10, 7, 18, 0)
    toolbox = Toolbox(clinic, kb, SqliteAgenda(kb, lambda: now), lambda: now, None)
    result, failed = _call(toolbox, "get_availability", date_from="2026-10-12",
                           date_to="2026-10-12", part_of_day="any")
    assert not failed and result["slots"] == []
    assert result["closed"] == ["lunes 2026-10-12: Fiesta Nacional"]
    week, _ = _call(toolbox, "get_availability", date_from="2026-10-12",
                    date_to="2026-10-16", part_of_day="morning")
    assert week["closed"] == ["lunes 2026-10-12: Fiesta Nacional"]
    assert not any(slot["start"].startswith("2026-10-12") for slot in week["slots"])
    assert "lunes 2026-10-12" not in week["free_days"]
    open_week, _ = _call(toolbox, "get_availability", date_from="2026-10-13",
                         date_to="2026-10-16", part_of_day="morning")
    assert "closed" not in open_week
