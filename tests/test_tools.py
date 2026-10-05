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
NOTHING = {"name": None, "name_spelled": False, "pet_name": None, "pet_confirmed": False,
           "town": None}


@pytest.fixture(scope="module")
def kb():
    return load_kb()


def _toolbox(clinic, kb, number=None):
    return Toolbox(clinic, kb, SqliteAgenda(kb, lambda: NOW), lambda: NOW, number)


def _call(toolbox, tool, /, **arguments):
    result = toolbox.run(ToolCall("call-1", tool, arguments))
    return json.loads(result.content), result.is_error


def _identify(toolbox, **given):
    if given.get("town"):
        toolbox.heard(f"Vivo en {given['town']}.")  # a town counts only once the caller says it
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
    toolbox = _toolbox(clinic, kb)
    toolbox.heard("Es para Yuna.")
    refused, failed = _call(toolbox, "identify_client", **{**NOTHING, "pet_name": "Yuna",
                                                           "pet_confirmed": True})
    assert failed and "neither spelled that name nor said it twice" in refused["error"]
    toolbox.heard("Sí, Yuna.")
    assert "error" not in _identify(toolbox, pet_name="Yuna", pet_confirmed=True)
    spelled = _toolbox(clinic, kb)
    spelled.heard("L-L-U-N-A")
    assert "error" not in _identify(spelled, pet_name="Lluna", pet_confirmed=True)
    # Unconfirmed, a name needs no backing: the resolver keeps its doubts.
    assert "error" not in _identify(_toolbox(clinic, kb), pet_name="Yuna")


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


def test_a_field_left_out_means_not_said(clinic, kb, scenarios):
    scenario = _by_phone(scenarios)
    toolbox = _toolbox(clinic, kb, scenario.call.caller_number)
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
    assert result["say_es"] == "lunes 9 de noviembre a las nueve y media de la mañana"
    assert result["say_ca"] == "dilluns 9 de novembre a les nou i mitja del matí"
    booked = toolbox.agenda.get(result["appointment_id"])
    assert booked.verified and booked.client_code == toolbox.session.client.code
    assert booked.pet_name == pet and booked.animal_code is not None
    assert "note" not in result


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
    listed, _ = _call(toolbox, "list_appointments")
    assert [a["appointment_id"] for a in listed["appointments"]] == [own.appointment_id]
    moved, failed = _call(toolbox, "reschedule_appointment",
                          appointment_id=own.appointment_id, new_start="2026-11-11T11:00")
    assert not failed and moved["start"] == "2026-11-11T11:00"
    cancelled, failed = _call(toolbox, "cancel_appointment", appointment_id=own.appointment_id)
    assert not failed and cancelled["status"] == "cancelled"
    assert _call(toolbox, "list_appointments")[0] == {"appointments": []}


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
        "say_es": "lunes 9 de noviembre a las cuatro y media de la tarde",
        "say_ca": "dilluns 9 de novembre a les quatre i mitja de la tarda",
    }
    assert _call(toolbox, "get_availability", date_from="2026-11-08", date_to="2026-11-08",
                 part_of_day="any")[0]["slots"] == []


def test_free_times_are_a_sample_over_several_days_and_say_so(clinic, kb):
    """Found by the evaluation harness: given the six earliest times, all on a Monday, the
    model told callers there was nothing else that week."""
    toolbox = _toolbox(clinic, kb)
    week, _ = _call(toolbox, "get_availability", date_from="2026-11-09", date_to="2026-11-13",
                    part_of_day="any")
    assert [slot["say_es"].split()[0] for slot in week["slots"]] == \
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
    refused, failed = _call(toolbox, "book_appointment", **booking)
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

    known = _toolbox(clinic, kb, "+34600999888")
    _call(known, "take_message", message="Que la llamen", contact_name="Ana", contact_phone=None)
    assert known.session.messages[0].contact_phone == "+34600999888"


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
