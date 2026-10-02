"""A run: stored as it goes, carried on where it stopped, reported from what is stored."""

import json
from types import SimpleNamespace

import pytest

from vetdesk.cli import main
from vetdesk.evals.record import CallRecord, Judgement, Tokens
from vetdesk.evals.report import format_report, summarize
from vetdesk.evals.run import MAX_ERRORS_IN_A_ROW, Models, RunStore, run, verdicts
from vetdesk.kb import load_kb

MODELS = Models("claude-sonnet-5-5", "claude-haiku-4-5", "claude-opus-5-5")


@pytest.fixture(scope="module")
def kb():
    return load_kb()


def _call(scenario, rep=0, ended="hung_up", error=None):
    return CallRecord(
        scenario_id=scenario.id, rep=rep, agent_model=MODELS.agent, caller_model=MODELS.caller,
        greeting="Hola.", exchanges=[], ended=ended, error=error, confirmed_client_code=None,
        appointments=[], messages=[],
        agent_tokens=Tokens(input=1000, output=100, cache_read=9000),
        caller_tokens=Tokens(input=500, output=50),
    )


def _judgement(record, scenario):
    return Judgement(
        scenario_id=record.scenario_id, rep=record.rep, judge_model=MODELS.judge,
        identity_questions=1, transfer_promised=[], unsupported_statements=[],
        false_action_claims=[], wrong_language=[], question_answered=None,
        caller_off_script=None, discarded=0, tokens=Tokens(input=2000, output=300),
    )


def test_a_run_is_stored_and_not_played_twice(tmp_path, scenarios):
    played = []

    def play(scenario, rep):
        played.append((scenario.id, rep))
        return _call(scenario, rep)

    some = scenarios[:5]
    assert run(some, RunStore(tmp_path), play, _judgement, reps=2, workers=3)
    assert sorted(played) == sorted((s.id, rep) for s in some for rep in (0, 1))

    store = RunStore(tmp_path)  # read back from disk
    assert len(store.calls) == len(store.judgements) == 10
    assert store.calls[some[0].id, 1].agent_tokens.cache_read == 9000

    played.clear()
    assert run(scenarios[:7], store, play, _judgement, reps=2, workers=3)
    assert sorted({scenario_id for scenario_id, _ in played}) == [s.id for s in scenarios[5:7]]

    played.clear()  # on request, calls already stored are played afresh
    assert run(scenarios[:2], store, play, _judgement, again=True)
    assert sorted(played) == [(scenarios[0].id, 0), (scenarios[1].id, 0)]
    assert len(RunStore(tmp_path).calls) == 14


def test_a_broken_call_is_retried_and_an_unjudged_one_is_judged(tmp_path, scenarios):
    some = scenarios[:3]
    failing = {some[1].id}

    def play(scenario, rep):
        if scenario.id in failing:
            return _call(scenario, rep, ended="error", error="LLMError: 529 overloaded")
        return _call(scenario, rep)

    def no_judge(record, scenario):
        raise RuntimeError("the judge is down")

    lines = []
    store = RunStore(tmp_path)
    assert run(some, store, play, no_judge, report=lines.append)
    assert store.calls[some[1].id, 0].ended == "error" and not store.judgements
    assert any("529 overloaded" in line for line in lines)
    assert sum("not judged" in line for line in lines) == 2  # a broken call is not judged

    failing.clear()
    store = RunStore(tmp_path)
    assert run(some, store, play, _judgement)
    assert store.calls[some[1].id, 0].ended == "hung_up" and len(store.judgements) == 3
    # The file keeps both attempts; the later one is the one that counts.
    assert len((tmp_path / "calls.jsonl").read_text().splitlines()) == 4
    assert RunStore(tmp_path).calls[some[1].id, 0].ended == "hung_up"


def test_a_run_stops_when_calls_keep_failing(tmp_path, scenarios):
    played = []

    def out_of_credit(scenario, rep):
        played.append(scenario.id)
        return _call(scenario, rep, ended="error", error="LLMError: credit balance is too low")

    finished = run(scenarios[:30], RunStore(tmp_path), out_of_credit, None, workers=1)
    assert not finished and len(played) == MAX_ERRORS_IN_A_ROW


def test_the_report_counts_what_must_be_zero_over_every_played_call(scenarios, truth, kb):
    """A false identification counts even in a call that is otherwise set aside."""
    borrowed = next(s for s in scenarios if s.category == "identity.borrowed_phone")
    trap = borrowed.expected.identity.forbidden_client_ids[0]
    info = next(s for s in scenarios if s.category == "info.no_identity_needed")
    wrong = _call(borrowed, ended="turn_limit").model_copy(
        update={"confirmed_client_code": truth.client_code(trap)})
    calls = [wrong, _call(info), _call(scenarios[0], ended="error", error="boom")]

    stored = SimpleNamespace(calls={(c.scenario_id, c.rep): c for c in calls},
                             judgements={(info.id, 0): _judgement(calls[1], info)})
    used = [borrowed, info, scenarios[0]]
    scored = verdicts(used, stored, truth, kb)
    summary = summarize(scored, {s.id: s for s in used})
    assert summary.safety["false_identification"] == 1
    assert (len(summary.played), len(summary.scored), summary.judged) == (2, 1, 1)

    report = format_report(summary, MODELS, calls, list(stored.judgements.values()))
    assert "false identifications                     1" in report
    assert "scored 1   broken by a technical error 1   never ended 1" in report
    assert "judge claude-opus-5-5" in report
    assert "[judge: claude-opus-5-5; 1 calls judged]" in report
    assert f"{borrowed.id}  identity.borrowed_phone  [unfinished]" in report
    assert f"false_identification: confirmed {trap}" in report
    # 3 calls of Sonnet: 3000 fresh + 27000 cached input, 300 output; judge: Opus.
    assert "agent $0.01" in report and "90% of its input read from cache" in report
    assert "judge $0.01" in report


def test_a_report_without_a_judge_says_so(scenarios, truth, kb):
    stored = SimpleNamespace(calls={(scenarios[0].id, 0): _call(scenarios[0])}, judgements={})
    scored = verdicts(scenarios[:1], stored, truth, kb)
    report = format_report(summarize(scored, {scenarios[0].id: scenarios[0]}),
                           Models("claude-sonnet-5-5", "claude-haiku-4-5", None),
                           list(stored.calls.values()), [])
    assert "judge none" in report and "not judged" in report and "[judge]" not in report


def test_report_and_show_work_from_what_is_stored(tmp_path, capsys):
    """Scoring again costs nothing: no model is needed to read a finished run."""
    from vetdesk.scenario import load_jsonl

    main(["generate", "--out", str(tmp_path)])
    scenarios = load_jsonl((tmp_path / "scenarios.jsonl").read_text(encoding="utf-8"))
    run_dir = tmp_path / "runs" / "one"
    store = RunStore(run_dir)
    store.write_info(MODELS, {"scenarios": "abc"})
    for scenario in scenarios[:3]:
        store.add_call(_call(scenario))
    capsys.readouterr()

    assert main(["eval", "report", str(run_dir), "--data", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "EVALUATION  3 calls   agent claude-sonnet-5-5" in out
    assert "identity.phone_and_name" in out

    assert main(["eval", "show", str(run_dir), scenarios[0].id, "--data", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "AGENT (greeting): Hola." in out and "task: missing" in out
    assert main(["eval", "show", str(run_dir), "S-999", "--data", str(tmp_path)]) == 1
    assert main(["eval", "report", str(tmp_path), "--data", str(tmp_path)]) == 1
    assert json.loads((run_dir / "run.json").read_text())["models"]["judge"] == MODELS.judge
