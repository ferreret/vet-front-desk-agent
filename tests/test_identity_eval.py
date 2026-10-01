"""The headline measurement: the resolver never confirms the wrong person.

No LLM here. The resolver reads the dirty database and what speech recognition heard; the
ground truth comes from the clean world and what was really said.
"""

from dataclasses import replace
from pathlib import Path

import pytest

from vetdesk.evals.identity import Probe, probes_from_scenarios, run_probe, summarize
from vetdesk.evals.sweep import sweep_probes
from vetdesk.identity import IdentityResolver
from vetdesk.legacy import LegacySqliteSource
from vetdesk.synth import GeneratorConfig, generate_world
from vetdesk.synth.legacy_db import write_legacy_db
from vetdesk.synth.scenarios import generate_scenarios


def _run(resolver, probes, client_ids):
    return summarize([run_probe(resolver, probe, client_ids) for probe in probes])


def test_scenarios_have_no_false_identification(clinic, client_ids, scenarios):
    summary = _run(IdentityResolver(clinic), probes_from_scenarios(scenarios), client_ids)
    assert summary.total == sum(s.expected.identity.outcome != "not_required" for s in scenarios)
    assert summary.false_identifications == 0
    assert summary.verdicts["unsupported_identification"] == 0
    assert summary.verdicts["coincidence"] == 0
    assert summary.identified / summary.identifiable > 0.9
    assert summary.over_asked <= 3


def test_every_trap_category_is_handled_exactly(clinic, client_ids, scenarios):
    """Where the expected outcome is NOT to identify, the resolver agrees on why."""
    resolver = IdentityResolver(clinic)
    for probe in probes_from_scenarios(scenarios):
        if probe.expected_outcome != "resolved":
            result = run_probe(resolver, probe, client_ids)
            assert result.outcome == probe.expected_outcome, probe.id


def test_sweep_has_no_false_identification(world, clinic, client_ids):
    probes = sweep_probes(world, strangers=1500)
    assert len(probes) > 8000
    summary = _run(IdentityResolver(clinic), probes, client_ids)
    assert summary.false_identifications == 0
    assert summary.verdicts["unsupported_identification"] == 0
    assert summary.identified / summary.identifiable > 0.95
    # Asking more than a perfect listener would is a cost, and it stays small.
    assert summary.over_asked / summary.total < 0.02


@pytest.mark.parametrize("seed,clients", [(7, 200), (11, 300)])
def test_other_clinics_have_no_false_identification(seed, clients, tmp_path):
    world = generate_world(GeneratorConfig(seed=seed, n_clients=clients))
    path = Path(tmp_path) / "clinic.db"
    write_legacy_db(world, path)
    resolver = IdentityResolver(LegacySqliteSource(path).load())
    client_ids = {c.legacy_codigo: c.client_id for c in world.clients.values()}
    probes = probes_from_scenarios(generate_scenarios(world)) + sweep_probes(world, strangers=500)
    summary = _run(resolver, probes, client_ids)
    assert summary.false_identifications == 0
    assert summary.verdicts["unsupported_identification"] == 0


# --- how a probe is judged -----------------------------------------------------------------------


def _probe(scenarios, category) -> Probe:
    return next(p for p in probes_from_scenarios(scenarios) if p.category == category)


def _confirmed_probe(clinic, client_ids, scenarios) -> Probe:
    """A call the resolver does confirm, to be judged against a doctored truth."""
    resolver = IdentityResolver(clinic)
    for probe in probes_from_scenarios(scenarios):
        if run_probe(resolver, probe, client_ids).outcome == "resolved":
            return probe
    raise AssertionError("the resolver confirmed nobody")


def test_confirming_someone_else_is_a_false_identification(clinic, client_ids, scenarios):
    probe = _confirmed_probe(clinic, client_ids, scenarios)
    lie = replace(probe, caller_id="C-9999", expected_outcome="not_a_client",
                  expected_client_id=None)
    assert run_probe(IdentityResolver(clinic), lie, client_ids).verdict == "false_identification"


def test_confirming_the_right_person_too_early_is_unsupported(clinic, client_ids, scenarios):
    probe = _confirmed_probe(clinic, client_ids, scenarios)
    strict = replace(probe, expected_outcome="unresolved", expected_client_id=None)
    result = run_probe(IdentityResolver(clinic), strict, client_ids)
    assert result.verdict == "unsupported_identification"


def test_questions_are_counted(clinic, client_ids, scenarios):
    resolver = IdentityResolver(clinic)
    by_phone = run_probe(resolver, _probe(scenarios, "identity.homonym_with_phone"), client_ids)
    assert by_phone.questions >= 1
    partial = _probe(scenarios, "identity.partial_name")
    assert partial.full_name_said and partial.full_name_said != partial.name_said
    result = run_probe(resolver, partial, client_ids)
    assert result.verdict == "correct" and result.questions >= 3
    tight = replace(partial, max_questions=1)
    assert run_probe(resolver, tight, client_ids).over_asked
