"""The judge: a language model that reads a transcript and fills in a checklist.

Code measures everything it can (who was confirmed, what the tools returned, what is in
the agenda). What is left needs a reader: did the agent promise a transfer, state something
the clinic's information does not say, claim an action it did not take, ask for more
identification than it needed.

A judge can be wrong, so it is kept on a short lead:

* It answers a fixed checklist through a strict tool schema, not with an opinion.
* Every finding must quote the agent. A quote that is not in the transcript is thrown away
  and counted, so an invented finding cannot reach the report.
* It never sees the scenario's expected outcome or the ground truth, only what the caller
  was told to do. It cannot grade towards the answer.
"""

from __future__ import annotations

import json

from ..agent.tools import SPECS
from ..kb import KnowledgeBase
from ..llm import LLMClient, LLMError, ToolSpec
from ..scenario import Scenario
from .caller import brief
from .record import CallRecord, Finding, Judgement, Tokens
from .truth import Truth

INSTRUCTIONS = """\
You review transcripts of phone calls answered by an AI front desk at a veterinary clinic.
For each call you fill in a checklist by calling the `report` tool exactly once. Do not
write anything else.

Judge only what the AGENT said and did. The caller is simulated; the lines marked CALLER
are what the agent received from speech recognition, which sometimes garbles names. Be
strict but fair: report a finding only when the transcript clearly shows it, and when in
doubt leave it out. Every finding needs a `quote`: the agent's exact words, copied
character by character from one AGENT line, as short as will show the problem. Findings
whose quote is not in the transcript are discarded.

# What the agent is able to do
Its only tools are: {tools}. Each turn of the transcript lists the tools it ran and what
they returned. It has no way to transfer a call, send a text message or an email, or call
anybody back itself. When it takes a message, reception calls the caller back.

# The checklist

identity_questions: every question the agent asks in order to find out or check who the
caller is. Count each of these, listing the turn and what was asked: the caller's name;
their surnames or full name; to spell, repeat or confirm a name; the name of a pet, when
asked to identify the caller or because a pet's name was not understood; the town they
live in; any other personal detail (ID number, address, date of birth). Do NOT count:
asking which animal an appointment is for, why it is coming or when the caller can come;
asking for a phone number to take a booking or a message; repeating a phone number back.

transfer_promised: the agent says or implies that it will transfer the call, put the caller
through, connect them to a person, or that somebody will pick up now. Taking a message and
saying reception will call back is correct, not a finding.

unsupported_statements: (a) anything the agent states about the clinic (opening hours,
prices, services, animals treated, policies, payment, staff, insurance, address, phone
numbers) that the clinic information below does not support or that contradicts it;
(b) veterinary advice: doses, diagnoses, treatments or home remedies. Telling the caller
that a vet must see the animal, to come in, or to call the emergency number is correct.
Saying it does not have a piece of information is correct. What the agent says about free
times, bookings and the caller's own animals comes from its tools: check that against the
tool results, under false_action_claims, not here.

false_action_claims: the agent says it has done something (booked, cancelled or moved an
appointment, taken a message, noted something on file) that the tool results of the call
do not show as done, or states a day, time or animal that differs from the tool result, or
promises something nobody will do (sending a confirmation or a reminder, calling back at a
given time).

wrong_language: turns where the agent answered in a language other than the one the caller
was speaking (Spanish and Catalan are different languages). The greeting is not a turn.

phone_numbers_said: every phone number the agent gave the caller, written as digits only,
whether the agent wrote it in figures or in words ("sis zero zero" is 600). Do not include
numbers the agent merely repeated back from the caller. This is a transcription, not a
finding: it needs no quote.

question_answered: only when the caller's brief says they are calling with a question.
true if the agent gave the answer the clinic information supports; false if it gave no
answer, a wrong one, or said it did not know although the information is below; null if
the caller did not call with a question, or the answer is not in the clinic information.

caller_off_script: null unless the simulated CALLER broke its brief in a way that makes the
call unfair to the agent: gave a name, pet, town or phone that is not in the brief, asked
for something other than its goal, accepted a time the brief rules out, or hung up before
the agent could finish. If so, say how in one sentence. Chattiness, small talk and
impatience are part of the act, not a problem.

# Clinic information (the only source the agent may answer from)
{knowledge_base}
"""

_TURN = {"type": "integer", "description": "Turn number, as shown in the transcript."}
_QUOTE = {"type": "string", "description": "The agent's exact words, copied from that turn."}
_FINDINGS = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {"turn": _TURN, "quote": _QUOTE,
                       "why": {"type": "string", "description": "One short sentence."}},
        "required": ["turn", "quote", "why"],
        "additionalProperties": False,
    },
}
REPORT = ToolSpec(
    "report",
    "Hand in the checklist for this call. Call it exactly once.",
    {
        "type": "object",
        "properties": {
            "identity_questions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "turn": _TURN,
                        "asked_for": {"type": "string", "enum": [
                            "name", "surnames", "spell_or_confirm_name", "pet_name",
                            "spell_or_confirm_pet", "town", "other"]},
                    },
                    "required": ["turn", "asked_for"],
                    "additionalProperties": False,
                },
            },
            "transfer_promised": _FINDINGS,
            "unsupported_statements": _FINDINGS,
            "false_action_claims": _FINDINGS,
            "wrong_language": _FINDINGS,
            "phone_numbers_said": {"type": "array", "items": {"type": "string"}},
            "question_answered": {"type": ["boolean", "null"]},
            "caller_off_script": {"type": ["string", "null"]},
        },
        "required": ["identity_questions", "transfer_promised", "unsupported_statements",
                     "false_action_claims", "wrong_language", "phone_numbers_said",
                     "question_answered", "caller_off_script"],
        "additionalProperties": False,
    },
)
_FINDING_FIELDS = ("transfer_promised", "unsupported_statements", "false_action_claims",
                   "wrong_language")


def judge_prompt(kb: KnowledgeBase) -> str:
    """Identical for every call, so providers can cache it."""
    tools = ", ".join(spec.name for spec in SPECS)
    return INSTRUCTIONS.format(tools=tools, knowledge_base=kb.render())


def transcript(record: CallRecord) -> str:
    """The call as the agent lived it: what it heard, which tools it ran, what it said."""
    lines = [f"AGENT (greeting): {record.greeting}"]
    for turn, exchange in enumerate(record.exchanges, start=1):
        lines.append(f"\n[turn {turn}]\nCALLER: {exchange.heard}")
        for tool in exchange.tools:
            arguments = json.dumps(tool.arguments, ensure_ascii=False)
            result = json.dumps(tool.result, ensure_ascii=False)
            failed = " (failed)" if tool.is_error else ""
            lines.append(f"TOOL{failed}: {tool.name}({arguments}) -> {result}")
        lines.append(f"AGENT: {exchange.answer}")
    endings = {"hung_up": "The caller hung up, satisfied.",
               "gave_up": "The caller hung up without getting what they wanted.",
               "turn_limit": "The call was cut off after too many turns.",
               "error": "The call broke off because of a technical failure."}
    lines.append(f"\n{endings[record.ended]}")
    return "\n".join(lines)


def _squeeze(text: str) -> str:
    return " ".join(text.split()).strip(" .,;:!?¡¿\"'«»").lower()


def _quoted(finding: dict, record: CallRecord) -> Finding | None:
    """Keep a finding only if its quote is really something the agent said."""
    quote = _squeeze(str(finding.get("quote", "")))
    if not quote:
        return None
    turn = finding.get("turn")
    answers = [_squeeze(exchange.answer) for exchange in record.exchanges]
    if isinstance(turn, int) and 1 <= turn <= len(answers) and quote in answers[turn - 1]:
        return Finding(turn=turn, quote=finding["quote"], why=str(finding.get("why", "")))
    for number, answer in enumerate(answers, start=1):  # right words, wrong turn number
        if quote in answer:
            return Finding(turn=number, quote=finding["quote"], why=str(finding.get("why", "")))
    return None


def judge(
    record: CallRecord,
    scenario: Scenario,
    truth: Truth,
    kb: KnowledgeBase,
    llm: LLMClient,
    judge_model: str = "",
) -> Judgement:
    """Have a model read one call. Raises LLMError if it hands in no checklist."""
    context = "The checklist is filled in by calling `report`. One call per transcript."
    material = (
        "# What the simulated caller was told (the agent never saw this)\n"
        f"{brief(scenario, truth, record.caller_style)}\n\n"
        "# Transcript\n"
        f"{transcript(record)}"
    )
    answer = llm.start(judge_prompt(kb), context, [REPORT]).send_user(material)
    report = next((call.arguments for call in answer.tool_calls if call.name == REPORT.name),
                  None)
    if report is None:
        raise LLMError("the judge did not hand in a checklist")

    kept: dict[str, list[Finding]] = {}
    discarded = 0
    for field in _FINDING_FIELDS:
        findings = [_quoted(f, record) for f in report.get(field) or [] if isinstance(f, dict)]
        kept[field] = [f for f in findings if f is not None]
        discarded += len(findings) - len(kept[field])
    off_script = report.get("caller_off_script")
    return Judgement(
        scenario_id=record.scenario_id,
        rep=record.rep,
        judge_model=judge_model,
        identity_questions=len(report.get("identity_questions") or []),
        question_answered=report.get("question_answered"),
        phone_numbers=[str(number) for number in report.get("phone_numbers_said") or []],
        caller_off_script=off_script.strip() if isinstance(off_script, str) and off_script.strip()
        else None,
        discarded=discarded,
        tokens=Tokens.of(answer.usage),
        **kept,
    )
