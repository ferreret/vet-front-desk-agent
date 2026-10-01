"""Call scenarios: coverage, reproducibility and internal consistency of the ground truth."""

from collections import Counter

import pytest

from vetdesk.scenario import dump_jsonl, load_jsonl
from vetdesk.synth import GeneratorConfig, generate_world
from vetdesk.synth.scenarios import PLAN, generate_scenarios


def _of(scenarios, prefix):
    return [s for s in scenarios if s.category.startswith(prefix)]


def test_every_category_is_covered(scenarios):
    assert Counter(s.category for s in scenarios) == dict(PLAN)
    assert [s.id for s in scenarios] == [f"S-{n:03d}" for n in range(1, len(scenarios) + 1)]


def test_every_pilot_failure_has_scenarios(scenarios):
    assert {s.pilot_failure for s in scenarios} >= {"P1", "P2", "P3", "P4", "P5"}


def test_scenarios_are_reproducible(world, scenarios):
    assert generate_scenarios(generate_world(GeneratorConfig(seed=42))) == scenarios
    assert generate_scenarios(generate_world(GeneratorConfig(seed=43))) != scenarios


def test_jsonl_round_trip(scenarios):
    assert load_jsonl(dump_jsonl(scenarios)) == scenarios


@pytest.mark.parametrize("seed,clients", [(1, 120), (2, 250), (3, 400), (4, 800)])
def test_other_clinics_still_cover_every_category(seed, clients):
    world = generate_world(GeneratorConfig(seed=seed, n_clients=clients))
    assert len(generate_scenarios(world)) == sum(count for _, count in PLAN)


def test_both_languages_and_all_noise_levels_appear(scenarios):
    assert {s.language for s in scenarios} == {"es", "ca"}
    assert {s.speech.noise for s in scenarios} == {"none", "light", "heavy"}


# --- identity ground truth ----------------------------------------------------------------


def test_the_expected_client_is_always_the_real_caller(scenarios):
    for s in scenarios:
        identity = s.expected.identity
        if identity.outcome == "resolved":
            assert identity.client_id == s.caller.client_id, s.id
        else:
            assert identity.client_id is None, s.id
        assert s.caller.client_id not in identity.forbidden_client_ids, s.id
        assert identity.client_id not in s.expected.privacy.must_not_reveal_about, s.id


def test_someone_who_is_not_a_client_is_never_identified(scenarios):
    strangers = [s for s in scenarios if s.caller.client_id is None]
    assert strangers
    for s in strangers:
        assert s.expected.identity.outcome in ("not_a_client", "unresolved", "not_required"), s.id


def test_a_phone_number_alone_never_confirms_anybody(scenarios):
    for s in scenarios:
        for step in s.identity_trace:
            if step.evidence.type == "caller_number":
                assert step.expect.decision == "ask", s.id
                assert step.expect.level != "confirmed", s.id


def test_traces_follow_the_call(scenarios):
    outcome_of = {"resolved": "resolved", "not_found": "not_a_client", "ask": "unresolved"}
    for s in scenarios:
        trace = s.identity_trace
        if s.expected.identity.outcome == "not_required":
            assert trace == [], s.id
            continue
        assert all(step.expect.decision == "ask" for step in trace[:-1]), s.id
        assert outcome_of[trace[-1].expect.decision] == s.expected.identity.outcome, s.id
        has_number = trace[0].evidence.type == "caller_number"
        assert has_number == (s.call.caller_number is not None), s.id
        assert (s.call.number_relation == "hidden") == (s.call.caller_number is None), s.id


def test_every_reference_exists_in_the_clinic(world, scenarios):
    for s in scenarios:
        expected = s.expected
        client_ids = [
            s.caller.client_id, expected.identity.client_id, s.caller.goal.about_client_id,
            *expected.identity.forbidden_client_ids, *expected.privacy.must_not_reveal_about,
            *(a.client_id for a in expected.actions),
            *(step_id for step in s.identity_trace for step_id in step.expect.consistent_with),
        ]
        assert all(i in world.clients for i in client_ids if i), s.id
        pet_ids = [s.caller.goal.pet_id, *(p.pet_id for p in s.caller.pets),
                   *(a.pet_id for a in expected.actions)]
        assert all(i in world.pets for i in pet_ids if i), s.id


def test_speech_noise_matches_its_label(scenarios):
    for s in scenarios:
        changed = any(u.said != u.heard for u in s.speech.utterances)
        assert changed == (s.speech.noise != "none"), s.id
        for step in s.identity_trace:
            if step.evidence.type == "caller_number":
                assert step.evidence.said == step.evidence.heard == s.call.caller_number
    assert all(s.speech.noise == "heavy" for s in _of(scenarios, "identity.heavy_asr_noise"))


# --- the traps each category is built around -------------------------------------------------


def test_shared_phone_tempts_with_a_housemate(world, scenarios):
    for s in _of(scenarios, "identity.shared_phone"):
        caller = world.clients[s.caller.client_id]
        traps = s.expected.identity.forbidden_client_ids
        housemates = [world.clients[i].household_id for i in traps]
        assert caller.household_id in housemates, s.id


def test_homonyms_with_hidden_number_stay_unconfirmed(scenarios):
    for s in _of(scenarios, "identity.homonym_hidden_number"):
        assert s.expected.identity.outcome == "unresolved"
        assert s.expected.identity.forbidden_client_ids


def test_same_household_homonyms_cover_both_outcomes(scenarios):
    pairs = _of(scenarios, "identity.homonym_same_household")
    outcomes = {s.expected.identity.outcome for s in pairs}
    assert outcomes == {"resolved", "unresolved"}


def test_inherited_number_points_at_a_client_who_is_not_calling(world, scenarios):
    for s in _of(scenarios, "identity.stale_phone_stranger"):
        assert s.caller.client_id is None
        assert world.stale_holders[s.call.caller_number] == "stranger"
        assert s.expected.identity.outcome == "not_a_client"
        assert s.expected.identity.forbidden_client_ids == world.phone_index()[s.call.caller_number]


def test_borrowed_phone_belongs_to_another_client(world, scenarios):
    for s in _of(scenarios, "identity.borrowed_phone"):
        owners = world.phone_index()[s.call.caller_number]
        assert s.caller.client_id not in owners
        assert set(owners) <= set(s.expected.identity.forbidden_client_ids)
        assert s.expected.identity.outcome == "resolved"


def test_changed_number_is_unknown_to_the_clinic(world, scenarios):
    for s in _of(scenarios, "identity.changed_number"):
        assert s.call.caller_number not in world.phone_index()
        assert s.expected.identity.outcome == "resolved"


def test_clients_without_animals(world, scenarios):
    for s in _of(scenarios, "identity.no_pets"):
        assert world.clients[s.caller.client_id].pet_ids == []
        hidden = s.call.caller_number is None
        assert s.expected.identity.outcome == ("unresolved" if hidden else "resolved"), s.id


def test_one_surname_leads_to_a_request_for_both(scenarios):
    for s in _of(scenarios, "identity.partial_name"):
        names = [step for step in s.identity_trace if step.evidence.type == "client_name"]
        assert [step.evidence.said for step in names] == [
            s.caller.says_name,
            f"{s.caller.given_name} {s.caller.surname1} {s.caller.surname2}",
        ]
        assert all(step.expect.decision == "ask" for step in names)
        assert s.identity_trace[-1].evidence.type == "pet_name"
        assert s.expected.identity.outcome == "resolved"
        assert len([u for u in s.speech.utterances if u.field == "client_name"]) == 2


def test_a_pet_cannot_confirm_a_record_with_a_single_surname(world, scenarios):
    for s in _of(scenarios, "identity.one_surname_on_file"):
        caller = world.clients[s.caller.client_id]
        assert not caller.surname2_on_file and caller.pet_ids
        assert s.expected.identity.outcome == "unresolved"
        assert s.identity_trace[-1].evidence.type == "pet_name"


def test_lookalikes_are_not_matched_to_the_existing_client(scenarios):
    lookalikes = _of(scenarios, "identity.lookalike_not_a_client")
    assert {s.expected.identity.outcome for s in lookalikes} == {"not_a_client", "unresolved"}
    for s in lookalikes:
        assert s.caller.client_id is None and s.expected.identity.forbidden_client_ids


# --- actions --------------------------------------------------------------------------------


def test_bookings_are_verified_only_for_confirmed_callers(world, scenarios):
    for s in scenarios:
        if s.caller.goal.type != "book":
            continue
        (action,) = s.expected.actions
        assert action.tool == "book_appointment"
        confirmed = s.expected.identity.outcome == "resolved"
        assert action.unverified == (not confirmed), s.id
        assert (action.client_id is not None) == confirmed, s.id
        if action.pet_id:
            assert world.pets[action.pet_id].owner_id == action.client_id, s.id
        assert action.window.date_from > s.clock.date()
        assert action.window.date_from.weekday() == 0 and action.window.date_to.weekday() == 4


def test_own_appointments_can_be_cancelled_and_moved(scenarios):
    for kind in ("cancel", "reschedule"):
        for s in _of(scenarios, f"agenda.{kind}_own"):
            (appointment,) = s.fixtures.appointments
            (action,) = s.expected.actions
            assert appointment.client_id == s.caller.client_id
            assert appointment.start > s.clock and appointment.start.weekday() < 5
            assert action.tool == f"{kind}_appointment"
            assert action.appointment_id == appointment.appointment_id
            assert (action.window is not None) == (kind == "reschedule")


def test_someone_elses_appointment_cannot_be_touched(scenarios):
    for s in _of(scenarios, "agenda.cancel_other"):
        (appointment,) = s.fixtures.appointments
        assert appointment.client_id != s.caller.client_id
        assert appointment.client_id in s.expected.privacy.must_not_reveal_about
        forbidden = {(f.tool, f.appointment_id) for f in s.expected.forbidden_actions}
        assert ("cancel_appointment", appointment.appointment_id) in forbidden
        assert all(a.optional and a.tool == "take_message" for a in s.expected.actions)


def test_third_party_questions_protect_the_other_client(scenarios):
    for s in _of(scenarios, "privacy.third_party_pet"):
        assert s.caller.goal.about_client_id in s.expected.privacy.must_not_reveal_about
        assert s.expected.actions == []


def test_asking_for_a_person_means_a_message_not_a_transfer(scenarios):
    for s in _of(scenarios, "handoff"):
        assert [a.tool for a in s.expected.actions] == ["take_message"]
        assert "transfer_to_human" in s.expected.forbidden_claims


def test_unknown_questions_must_not_be_answered_from_thin_air(scenarios):
    for s in _of(scenarios, "kb.unknown_question"):
        assert "invented_answer" in s.expected.forbidden_claims


def test_emergencies_get_the_number_without_an_interrogation(scenarios):
    for s in _of(scenarios, "kb.emergency_out_of_hours"):
        assert s.expected.must_include_facts == ["kb.emergency_phone"]
        assert s.expected.identity.max_questions == 0
        assert s.clock.hour >= 22 or s.clock.hour < 7


def test_calls_that_need_no_identity_reveal_nothing(world, scenarios):
    for s in scenarios:
        if s.expected.identity.outcome != "not_required":
            continue
        on_file = world.phone_index().get(s.call.caller_number, [])
        assert set(on_file) <= set(s.expected.privacy.must_not_reveal_about), s.id
