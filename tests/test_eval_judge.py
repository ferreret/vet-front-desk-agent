"""The judge, on a scripted model: what it is shown, and how its findings are checked."""

import pytest

from vetdesk.evals.caller import brief
from vetdesk.evals.judge import REPORT, judge, judge_prompt, transcript
from vetdesk.evals.record import CallRecord, Exchange, Tokens, ToolUse
from vetdesk.kb import load_kb
from vetdesk.llm import LLMError, Reply, ToolCall, Usage
from vetdesk.llm.scripted import ScriptedClient

EMPTY = {"identity_questions": [], "transfer_promised": [], "unsupported_statements": [],
         "false_action_claims": [], "wrong_language": [], "phone_numbers_said": [],
         "question_answered": None, "caller_off_script": None}


@pytest.fixture(scope="module")
def kb():
    return load_kb()


@pytest.fixture(scope="module")
def scenario(scenarios):
    return next(s for s in scenarios if s.category == "handoff.ask_for_human")


@pytest.fixture(scope="module")
def record(scenario):
    message = ToolUse(name="take_message",
                      arguments={"message": "Factura", "contact_name": "Maria Ma Sala",
                                 "contact_phone": None},
                      result={"status": "message_taken"}, is_error=False)
    exchanges = [
        Exchange(said="Vull parlar amb algú.", heard="Vull parlar amb algú.",
                 answer="No puc passar la trucada. Si vol, prenc nota. Em diu el seu nom?",
                 tools=[], confirmed=False, first_words=1.2, seconds=1.9, requests=[1.9]),
        Exchange(said="Maria Mas Sala.", heard="Maria Ma Sala.",
                 answer="Un moment.  Fet: recepció la trucarà. A les deu obrim dissabte.",
                 tools=[message], confirmed=False, first_words=0.9, seconds=3.1,
                 requests=[1.5, 1.6]),
    ]
    return CallRecord(
        scenario_id=scenario.id, rep=0, agent_model="a", caller_model="c",
        greeting="Clínica veterinaria Planeta Animal, buenos días.", exchanges=exchanges,
        ended="hung_up", confirmed_client_code=None, appointments=[], messages=[],
        agent_tokens=Tokens(), caller_tokens=Tokens(),
    )


def _judge_model(**found):
    return ScriptedClient([Reply("", (ToolCall("r", REPORT.name, {**EMPTY, **found}),),
                                 "tool_calls", Usage(3000, 200, 2500, 0))])


def test_the_judge_reads_what_the_agent_lived(record):
    text = transcript(record)
    assert text.startswith("AGENT (greeting): Clínica veterinaria Planeta Animal")
    assert "[turn 2]\nCALLER: Maria Ma Sala." in text  # what was heard, not what was said
    assert "Maria Mas Sala" not in text
    assert 'TOOL: take_message({"message": "Factura"' in text and '"message_taken"' in text
    assert text.endswith("The caller hung up, satisfied.")


def test_the_judge_never_sees_the_expected_outcome(record, scenario, truth, kb):
    model = _judge_model()
    judge(record, scenario, truth, kb, model, "judge-model")
    seen = model.transcript
    assert seen.system == judge_prompt(kb) and kb.render() in seen.system
    assert seen.tools == [REPORT]
    material = seen.user_messages[0]
    assert brief(scenario, truth) in material and transcript(record) in material
    for hidden in ("C-0", "transfer_to_human", "forbidden", "must_include", "max_questions"):
        assert hidden not in material and hidden not in seen.system


def test_findings_are_kept_only_with_a_real_quote(record, scenario, truth, kb):
    model = _judge_model(
        identity_questions=[{"turn": 1, "asked_for": "name"}],
        transfer_promised=[
            {"turn": 1, "quote": "Le paso con recepción", "why": "invented by the judge"},
        ],
        unsupported_statements=[
            # Right words, wrong turn number, sloppy spacing and case: still the agent's words.
            {"turn": 1, "quote": "a les deu obrim dissabte", "why": "not what the hours say"},
        ],
        false_action_claims=[
            {"turn": 2, "quote": "Fet: recepció la trucarà.", "why": "checked against the tool"},
            {"turn": 2, "quote": "", "why": "no quote at all"},
        ],
        question_answered=None,
    )
    judgement = judge(record, scenario, truth, kb, model, "judge-model")
    assert judgement.identity_questions == 1
    assert judgement.transfer_promised == []
    assert [(f.turn, f.quote) for f in judgement.unsupported_statements] == \
        [(2, "a les deu obrim dissabte")]
    assert [f.turn for f in judgement.false_action_claims] == [2]
    assert judgement.discarded == 2
    assert (judgement.judge_model, judgement.tokens.cache_read) == ("judge-model", 2500)
    assert judgement.caller_off_script is None


def test_a_caller_off_its_brief_is_reported(record, scenario, truth, kb):
    model = _judge_model(caller_off_script=" Gave a town that is not in its brief. ",
                         question_answered=True)
    judgement = judge(record, scenario, truth, kb, model)
    assert judgement.caller_off_script == "Gave a town that is not in its brief."
    assert judgement.question_answered is True
    assert judge(record, scenario, truth, kb, _judge_model(caller_off_script="  ")) \
        .caller_off_script is None


def test_a_judge_that_hands_in_nothing_is_an_error(record, scenario, truth, kb):
    with pytest.raises(LLMError):
        judge(record, scenario, truth, kb, ScriptedClient([Reply("The call went well.")]))


def test_the_checklist_schema_is_strict():
    def strict(schema):
        if schema.get("type") == "object":
            assert schema["additionalProperties"] is False
            assert schema["required"] == list(schema["properties"])
            for child in schema["properties"].values():
                strict(child)
        elif schema.get("type") == "array":
            strict(schema["items"])

    strict(REPORT.parameters)
    assert set(REPORT.parameters["properties"]) == set(EMPTY)
