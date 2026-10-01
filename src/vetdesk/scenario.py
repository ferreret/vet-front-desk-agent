"""The call-scenario contract shared by the generator (F1), identity tests (F2) and harness (F4).

A scenario has three readers:

* `identity_trace` feeds the identity resolver step by step, with no LLM involved (F2).
* `caller` and `speech` drive a simulated caller in a full conversation (F4).
* `expected` is the single definition of a correct outcome, used by both.

Free-text fields (`persona`, `topic`, `notes`) are in English; the simulated caller speaks
the scenario's `language`.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

SCHEMA_VERSION = 1

Language = Literal["es", "ca"]
NoiseLevel = Literal["none", "light", "heavy"]
NumberRelation = Literal[
    "own",  # the caller's own number, on file for them alone
    "household_shared",  # on file for several clients of the same household
    "own_not_on_file",  # the caller's current number; the clinic does not have it
    "third_party_client",  # borrowed phone that belongs to another client
    "stale_reassigned",  # still on a client's record, but someone else has it now
    "stranger",  # not on file, caller is not a client
    "hidden",  # no caller ID
]
PilotFailure = Literal["P1", "P2", "P3", "P4", "P5"]
Tool = Literal["book_appointment", "cancel_appointment", "reschedule_appointment", "take_message"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Call(_Model):
    caller_number: str | None  # E.164, or None when hidden
    number_relation: NumberRelation


class Window(_Model):
    date_from: date
    date_to: date
    part_of_day: Literal["morning", "afternoon", "any"]


class Goal(_Model):
    type: Literal[
        "book", "cancel", "reschedule", "cancel_other", "third_party_info",
        "human", "unknown_question", "emergency", "info",
    ]
    pet_id: str | None = None
    pet_name: str | None = None
    reason: str | None = None
    window: Window | None = None
    appointment_id: str | None = None
    about_client_id: str | None = None
    topic: str | None = None


class CallerPet(_Model):
    pet_id: str | None  # None when the clinic has no record of it
    name: str


class Caller(_Model):
    """Ground truth about who is on the line. Never shown to the agent."""

    client_id: str | None  # None when the caller is not a client
    given_name: str
    surname1: str
    surname2: str | None
    says_name: str  # what they answer when asked for their name
    town: str  # where they live
    pets: list[CallerPet]
    persona: str
    goal: Goal


class Utterance(_Model):
    field: Literal["client_name", "pet_name", "town"]
    said: str
    heard: str  # what speech recognition delivers


class Speech(_Model):
    noise: NoiseLevel
    utterances: list[Utterance]


class Evidence(_Model):
    type: Literal["caller_number", "client_name", "pet_name", "town"]
    said: str
    heard: str


class StepExpectation(_Model):
    decision: Literal["ask", "resolved", "not_found"]
    level: Literal["none", "probable", "confirmed"]
    client_id: str | None
    consistent_with: list[str]  # clients a perfect listener could still be talking to


class TraceStep(_Model):
    evidence: Evidence
    expect: StepExpectation


class ExpectedIdentity(_Model):
    outcome: Literal["resolved", "unresolved", "not_a_client", "not_required"]
    client_id: str | None
    # The clients this scenario tempts the agent to pick. Identifying the caller as any
    # client other than `client_id` is a false identification; these are the likely ones.
    forbidden_client_ids: list[str]
    max_questions: int | None  # identity questions before it counts as over-asking


class Privacy(_Model):
    must_not_reveal_about: list[str]


class ExpectedAction(_Model):
    tool: Tool
    client_id: str | None = None
    pet_id: str | None = None
    appointment_id: str | None = None
    reason: str | None = None
    window: Window | None = None
    unverified: bool = False  # booked for a caller the agent could not confirm
    optional: bool = False


class ForbiddenAction(_Model):
    tool: Tool
    appointment_id: str | None = None


class Expected(_Model):
    identity: ExpectedIdentity
    privacy: Privacy
    actions: list[ExpectedAction]
    forbidden_actions: list[ForbiddenAction]
    must_include_facts: list[str]  # knowledge-base keys, e.g. "kb.emergency_phone"
    forbidden_claims: list[str]  # e.g. "transfer_to_human", "invented_answer"


class Appointment(_Model):
    appointment_id: str
    client_id: str
    pet_id: str
    start: datetime
    reason: str


class Fixtures(_Model):
    appointments: list[Appointment]


class Scenario(_Model):
    schema_version: int = SCHEMA_VERSION
    id: str
    category: str
    pilot_failure: PilotFailure | None
    language: Language
    clock: datetime  # "now" for this call, so relative dates are deterministic
    call: Call
    caller: Caller
    speech: Speech
    identity_trace: list[TraceStep]
    expected: Expected
    fixtures: Fixtures
    notes: str


def dump_jsonl(scenarios: list[Scenario]) -> str:
    return "".join(s.model_dump_json() + "\n" for s in scenarios)


def load_jsonl(text: str) -> list[Scenario]:
    return [Scenario.model_validate_json(line) for line in text.splitlines() if line.strip()]
