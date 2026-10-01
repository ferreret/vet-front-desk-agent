from vetdesk.cli import main
from vetdesk.explain import Labels, explain
from vetdesk.synth.legacy_db import export_truth


def _first(scenarios, category):
    return next(s for s in scenarios if s.category == category)


def test_every_scenario_can_be_explained(world, scenarios):
    labels = Labels(export_truth(world))
    for s in scenarios:
        for text in (explain(s), explain(s, labels)):
            assert text.startswith(f"{s.id}  {s.category}")
            assert s.notes in text
            assert "None" not in text, s.id


def test_borrowed_phone_story_names_the_trap(world, scenarios):
    s = _first(scenarios, "identity.borrowed_phone")
    text = explain(s, Labels(export_truth(world)))
    (trap,) = s.expected.identity.forbidden_client_ids
    owner = world.clients[trap]
    assert "a phone borrowed from another client" in text
    assert f"{trap} ({owner.given} {owner.surname1} {owner.surname2})" in text
    assert "false identification" in text
    assert f"confirmed: {s.caller.client_id}" in text
    assert "book an appointment for" in text


def test_unconfirmed_callers_are_described_as_such(scenarios):
    text = explain(_first(scenarios, "identity.homonym_hidden_number"))
    assert "cannot be confirmed" in text
    assert "an unconfirmed caller" in text and "flagged for reception" in text


def test_calls_without_identification_say_so(scenarios):
    text = explain(_first(scenarios, "kb.emergency_out_of_hours"))
    assert "Not needed: this call can be handled without knowing who is calling." in text
    assert "kb.emergency_phone" in text and "transfer_to_human" in text
    assert "asking who is calling is itself the mistake" in text


def test_forbidden_actions_are_listed(scenarios):
    s = _first(scenarios, "agenda.cancel_other")
    text = explain(s)
    assert "ALREADY IN THE AGENDA" in text
    assert f"cancel appointment {s.fixtures.appointments[0].appointment_id}" in text
    assert "take a message for reception (optional)" in text


def test_explain_command(tmp_path, capsys):
    main(["generate", "--out", str(tmp_path)])
    capsys.readouterr()
    file = str(tmp_path / "scenarios.jsonl")
    assert main(["scenarios", "explain", "S-031", "--file", file]) == 0
    out = capsys.readouterr().out
    assert out.startswith("S-031  identity.borrowed_phone")
    assert "The caller says where they live" in out and "-> confirmed: C-" in out
    assert main(["scenarios", "explain", "S-999", "--file", file]) == 1
