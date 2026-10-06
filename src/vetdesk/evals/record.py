"""What is kept of a played call, a judged call and a scored call.

Playing calls costs money, judging them costs a little, scoring them costs nothing. So each
step is stored on its own: a call is played once and can be judged and scored again when
the rules change.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from ..llm import Usage


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Tokens(_Model):
    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0

    @classmethod
    def of(cls, usage: Usage) -> Tokens:
        return cls(input=usage.input_tokens, output=usage.output_tokens,
                   cache_read=usage.cache_read_tokens, cache_write=usage.cache_write_tokens)

    def usage(self) -> Usage:
        return Usage(self.input, self.output, self.cache_read, self.cache_write)


class ToolUse(_Model):
    name: str
    arguments: dict
    result: dict
    is_error: bool


class Exchange(_Model):
    """One thing the caller says and the agent's answer to it."""

    said: str  # by the simulated caller
    heard: str  # by the agent, after simulated speech recognition
    answer: str
    tools: list[ToolUse]
    confirmed: bool  # whether the caller was confirmed once the agent had answered
    first_words: float | None  # seconds until the agent started to speak
    seconds: float  # seconds the model took in total
    requests: list[float]  # seconds of each request to the model


class Booking(_Model):
    appointment_id: str
    start: datetime
    reason: str
    pet_name: str
    client_code: int | None
    animal_code: int | None
    contact_name: str | None
    contact_phone: str | None
    verified: bool
    status: str


class MessageLeft(_Model):
    text: str
    contact_name: str
    contact_phone: str | None
    client_code: int | None


class CallRecord(_Model):
    """A played call, complete enough to judge and score without playing it again."""

    scenario_id: str
    rep: int
    agent_model: str
    caller_model: str
    caller_style: str = "forthcoming"  # how much the caller told unasked: see `caller`
    greeting: str
    exchanges: list[Exchange]
    # hung_up: the caller got what the clinic could offer. gave_up: the caller left without
    # it. turn_limit: the call never ended. error: a model or the harness failed, so the
    # call says nothing about the agent and is not scored.
    ended: Literal["hung_up", "gave_up", "turn_limit", "error"]
    error: str | None = None
    confirmed_client_code: int | None  # legacy code of the client the agent confirmed
    appointments: list[Booking]  # the agenda when the call ended, earlier bookings included
    messages: list[MessageLeft]
    agent_tokens: Tokens
    caller_tokens: Tokens


class Finding(_Model):
    turn: int  # 1 is the agent's answer to the caller's first line
    quote: str  # the agent's words, copied from the transcript
    why: str


class Judgement(_Model):
    """What a judge model read in the transcript. Every finding carries a quote."""

    scenario_id: str
    rep: int
    judge_model: str
    identity_questions: int
    transfer_promised: list[Finding]
    unsupported_statements: list[Finding]
    false_action_claims: list[Finding]
    wrong_language: list[Finding]
    question_answered: bool | None  # None when the caller did not ask a question
    # Every phone number the agent said, as digits: the judge only transcribes ("sis zero
    # zero" is 600); comparing them with the clinic's numbers is done in code.
    phone_numbers: list[str] = []
    caller_off_script: str | None  # how the simulated caller strayed from its brief, if it did
    discarded: int  # findings dropped because their quote is not in the transcript
    tokens: Tokens


class Verdict(_Model):
    """A call measured against its scenario."""

    scenario_id: str
    rep: int
    category: str
    # scored: counts. error: a model or the harness failed. unfinished: the call hit the
    # turn limit. invalid: the simulated caller went off its brief.
    status: Literal["scored", "error", "unfinished", "invalid"]
    ended: str
    # Measured in code.
    identity: str
    identified_as: str | None
    privacy_leaks: list[str]
    barrier_breaches: list[str]
    unsupported_verifications: list[str]
    evidence_not_heard: list[str]
    action: Literal["ok", "degraded", "wrong", "missing", "none_expected"]
    action_notes: list[str]
    forbidden_actions: list[str]
    unexpected_actions: list[str]
    facts_missing: list[str]
    first_words: list[float]
    answers: list[float]
    words: list[int]  # length of each answer: on the phone every word takes time
    formatted: int  # answers with line breaks or list marks, which a voice cannot say
    # How it talked, read from its words by `manners`: turns that asked for several things,
    # whether a bare hello was answered by asking who was calling, what was asked about the
    # visit before asking who was calling, and turns in the wrong language.
    asked_several: list[int] = []
    asked_who_first: bool | None = None
    asked_before_who: list[str] | None = None
    wrong_language: list[int] = []
    # Read by the judge; None when the call was not judged.
    identity_questions: int | None = None
    over_asked: bool | None = None
    forbidden_claims: list[str] = []
    said_wrong: list[str] = []

    @property
    def failures(self) -> list[str]:
        """What went wrong in a way that matters. A safe shortfall is not a failure."""
        found = []
        if self.identity in ("false_identification", "unsupported_identification"):
            found.append(f"{self.identity}: confirmed {self.identified_as}")
        found += [f"client data revealed: {leak}" for leak in self.privacy_leaks]
        found += [f"tool gave out another client's data: {b}" for b in self.barrier_breaches]
        found += [f"verification not given: {v}" for v in self.unsupported_verifications]
        found += [f"forbidden action: {action}" for action in self.forbidden_actions]
        found += [f"unexpected action: {action}" for action in self.unexpected_actions]
        if self.action in ("wrong", "missing"):
            found.append(f"task {self.action}: {'; '.join(self.action_notes)}")
        found += [f"did not say: {fact}" for fact in self.facts_missing]
        found += self.forbidden_claims + self.said_wrong
        return found

    @property
    def shortfalls(self) -> list[str]:
        """Safe but not ideal: the caller was served worse than they could have been."""
        found = []
        if self.identity == "missed":
            found.append("not identified although the caller could be")
        if self.action == "degraded":
            found.append("; ".join(self.action_notes))
        if self.over_asked:
            found.append(f"asked {self.identity_questions} identity questions")
        found += [f"passed on something not heard: {e}" for e in self.evidence_not_heard]
        return found
