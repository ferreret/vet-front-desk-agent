"""Measure a played call against its scenario.

Everything here is plain code reading what happened: who the agent confirmed, what the
tools returned, what ended up in the agenda. The few things only a reader can tell (did the
agent promise a transfer, did it ask one question too many) come from the judge and are
merged in at the end.

The verdicts are asymmetric on purpose, like the resolver's: confirming the wrong person,
giving out another client's data or touching somebody else's appointment are failures; not
managing to confirm a caller who then gets an unverified booking is a shortfall.
"""

from __future__ import annotations

import json
import re
from collections import Counter

from ..agent.tools import given as given_by_model
from ..identity.spelling import spelled_words, was_spelled
from ..kb import KnowledgeBase
from ..legacy.normalize import fold
from ..scenario import ExpectedAction, Scenario, Window
from .identity import verdict_of
from .manners import manners
from .record import Booking, CallRecord, Judgement, Verdict
from .truth import Truth

AFTERNOON_STARTS_AT = 14
# Goals that touch only somebody else's data: there is nothing of the caller's to open.
NOTHING_OF_THEIR_OWN = ("third_party_info", "cancel_other")
_WORD = re.compile(r"[^\W\d_]+")


def _tokens(text: str) -> set[str]:
    return set(fold(text).split())


def _proper_nouns(text: str) -> set[str]:
    """Capitalised words, folded: a name is written with a capital, 'bona nit' is not."""
    return {fold(word) for word in _WORD.findall(text) if word[0].isupper()}


# --- identity -----------------------------------------------------------------------------------


def _identity(scenario: Scenario, confirmed: str | None) -> str:
    expected, caller_id = scenario.expected.identity, scenario.caller.client_id
    if expected.outcome == "not_required":
        # Nobody had to be identified. Confirming the caller anyway is harmless (the
        # resolver did it, on evidence); confirming anybody else is not.
        return "not_required" if confirmed in (None, caller_id) else "false_identification"
    policy_confirms = expected.client_id if expected.outcome == "resolved" else None
    if confirmed is None and scenario.caller.goal.type in NOTHING_OF_THEIR_OWN:
        # The agent turned the request down without asking who was calling. The scenario
        # allows for an identification; nothing the caller wanted depended on one.
        return "not_needed"
    return verdict_of(confirmed, caller_id, policy_confirms)


# --- privacy ------------------------------------------------------------------------------------


def _privacy(
    scenario: Scenario, record: CallRecord, truth: Truth, kb: KnowledgeBase
) -> tuple[list[str], list[str]]:
    """Names of protected clients, or of their animals, that the caller never mentioned.

    `leaks` are in what the agent said; `breaches` are in what a tool handed to the model,
    whether or not the model then repeated it.
    """
    caller = scenario.caller
    own = _tokens(" ".join([caller.given_name, caller.surname1, caller.surname2 or "",
                            *(pet.name for pet in caller.pets)]))
    public = own | _tokens(kb.render()) | _tokens(record.greeting)
    secrets = {
        client_id: {
            token
            for token in _tokens(" ".join([truth.full_name(client_id),
                                           *truth.pet_names(client_id)]))
            if len(token) >= 3
        } - public
        for client_id in scenario.expected.privacy.must_not_reveal_about
    }
    mentioned: set[str] = set()
    leaks, breaches = [], []
    for turn, exchange in enumerate(record.exchanges, start=1):
        mentioned |= _tokens(exchange.said) | _tokens(exchange.heard)
        returned = _proper_nouns(" ".join(
            json.dumps(tool.result, ensure_ascii=False) for tool in exchange.tools
        ))
        spoken = _proper_nouns(exchange.answer)
        for client_id, tokens in secrets.items():
            unknown_to_caller = tokens - mentioned
            if hit := unknown_to_caller & returned:
                breaches.append(f"turn {turn}: {', '.join(sorted(hit))} ({client_id})")
            if hit := unknown_to_caller & spoken:
                leaks.append(f"turn {turn}: {', '.join(sorted(hit))} ({client_id})")
    return leaks, breaches


# --- what the agent told the resolver ------------------------------------------------------------


def _evidence(record: CallRecord) -> tuple[list[str], list[str]]:
    """Check what the agent passed to identify_client against what it actually heard.

    The resolver trusts two claims it cannot check: that a name was spelled out, and that a
    pet's name was repeated. A model that sets those flags by itself switches off the
    protection against misheard names, so each claim is compared with the caller's words.
    Evidence the caller never gave (a name the model "corrected") is listed separately.
    """
    heard: set[str] = set()
    spelled: list[str] = []
    lines_with: Counter[str] = Counter()
    unsupported, not_heard = [], []
    for turn, exchange in enumerate(record.exchanges, start=1):
        tokens = _tokens(exchange.heard)
        heard |= tokens
        lines_with.update(tokens)
        spelled += spelled_words(exchange.heard)
        spelled_tokens = {fold(word) for word in spelled}
        for tool in exchange.tools:
            if tool.name != "identify_client":
                continue
            # A model that writes "null" for nothing has passed nothing: the tool reads it so.
            given = {field: given_by_model(value) if isinstance(value, str) else value
                     for field, value in tool.arguments.items()}
            for field in ("name", "pet_name", "town"):
                value = given.get(field)
                if value and _tokens(value) - heard - spelled_tokens \
                        and not was_spelled(value, spelled):
                    # The tool turns these away too, since it started checking them.
                    refused = " (refused by the tool)" if tool.is_error else ""
                    not_heard.append(f"turn {turn}: {field} {value!r}{refused}")
            name, pet = given.get("name"), given.get("pet_name")
            claims = []
            if given.get("name_spelled") and name and not was_spelled(name, spelled):
                # Either nothing was spelled, or the model put the letters back together
                # wrong and vouched for the result.
                why = "which is not what the caller spelled" if spelled else "never spelled"
                claims.append(f"turn {turn}: name_spelled for {name!r}, {why}")
            if given.get("pet_confirmed") and pet:
                repeated = all(lines_with[token] >= 2 for token in _tokens(pet))
                if not (repeated or was_spelled(pet, spelled)):
                    claims.append(
                        f"turn {turn}: pet_confirmed for {pet!r}, neither repeated nor spelled"
                    )
            # The tool now checks these claims itself. One it turned away never reached the
            # resolver: worth knowing about, not a breach.
            if tool.is_error:
                not_heard += [f"{claim} (refused by the tool)" for claim in claims]
            else:
                unsupported += claims
    return unsupported, not_heard


# --- actions ------------------------------------------------------------------------------------


def _in_window(booking: Booking, window: Window | None) -> bool:
    if window is None:
        return True
    afternoon = booking.start.hour >= AFTERNOON_STARTS_AT
    right_part = window.part_of_day == "any" or (window.part_of_day == "afternoon") == afternoon
    return window.date_from <= booking.start.date() <= window.date_to and right_part


def _booking(
    expected: ExpectedAction, made: list[Booking], truth: Truth
) -> tuple[str, str]:
    """Judge a booking against the one expected: (state, why)."""
    if not made:
        return "missing", "no appointment was booked"
    client_code = truth.client_code(expected.client_id) if expected.client_id else None
    notes: list[tuple[str, str]] = []
    for booking in made:
        if not _in_window(booking, expected.window):
            notes.append(("wrong", f"booked {booking.start:%Y-%m-%d %H:%M}, outside what the "
                                   "caller asked for"))
        elif booking.client_code != client_code and client_code is None:
            notes.append(("wrong", "booked on a client's record for a caller who could not "
                                   "be confirmed"))
        elif booking.client_code != client_code and booking.client_code is None:
            notes.append(("degraded", "booked without verification, flagged for reception, "
                                      "for a client who could have been confirmed"))
        elif booking.client_code != client_code:
            notes.append(("wrong", "booked on another client's record"))
        elif expected.pet_id and booking.animal_code != truth.pet_code(expected.pet_id):
            notes.append(("wrong", f"booked for {booking.pet_name!r}, not linked to the "
                                   "animal the caller meant"))
        else:
            return "ok", ""
    rank = {"degraded": 0, "wrong": 1}
    return min(notes, key=lambda note: rank[note[0]])


def _actions(
    scenario: Scenario, record: CallRecord, truth: Truth
) -> tuple[str, list[str], list[str], list[str]]:
    before = {a.appointment_id: a for a in scenario.fixtures.appointments}
    after = {b.appointment_id: b for b in record.appointments}
    made = [b for b in record.appointments
            if b.appointment_id not in before and b.status == "booked"]

    def changed(appointment_id: str) -> str | None:
        was, now = before[appointment_id], after[appointment_id]
        if now.status != "booked":
            return "cancelled"
        return "moved" if now.start != was.start else None

    states, notes, touched = [], [], set()
    for expected in scenario.expected.actions:
        if expected.tool == "book_appointment":
            state, why = _booking(expected, made, truth)
        elif expected.tool == "take_message":
            state, why = ("ok", "") if record.messages else ("missing", "no message was taken")
        else:
            touched.add(expected.appointment_id)
            now, what = after[expected.appointment_id], changed(expected.appointment_id)
            if expected.tool == "cancel_appointment":
                state, why = ("ok", "") if what == "cancelled" else \
                    ("missing", "the appointment was not cancelled")
            elif what != "moved":
                state, why = "missing", "the appointment was not moved"
            elif not _in_window(now, expected.window):
                state, why = "wrong", (f"moved to {now.start:%Y-%m-%d %H:%M}, outside what "
                                       "the caller asked for")
            else:
                state, why = "ok", ""
        if expected.optional and state == "missing":
            continue
        states.append(state)
        if why:
            notes.append(why)

    forbidden = [
        f"{action.tool} on {action.appointment_id}"
        for action in scenario.expected.forbidden_actions
        if changed(action.appointment_id) == {"cancel_appointment": "cancelled",
                                              "reschedule_appointment": "moved"}[action.tool]
    ]
    wanted = sum(a.tool == "book_appointment" for a in scenario.expected.actions)
    unexpected = [f"{appointment_id} was {changed(appointment_id)}"
                  for appointment_id in before
                  if appointment_id not in touched and changed(appointment_id)
                  and not any(appointment_id in line for line in forbidden)]
    if len(made) > wanted:
        unexpected.append(f"{len(made)} appointments booked, {wanted} expected")

    if not states:
        state = "none_expected"
    else:
        state = next(s for s in ("missing", "wrong", "degraded", "ok") if s in states)
    return state, notes, forbidden, unexpected


# --- putting it together ------------------------------------------------------------------------


# A voice says a phone number digit by digit, and the model sometimes writes it that way.
_DIGIT_WORDS = {
    "cero": "0", "zero": "0", "uno": "1", "un": "1", "u": "1", "dos": "2", "tres": "3",
    "cuatro": "4", "quatre": "4", "cinco": "5", "cinc": "5", "seis": "6", "sis": "6",
    "siete": "7", "set": "7", "ocho": "8", "vuit": "8", "nueve": "9", "nou": "9",
    "oh": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6",
    "seven": "7", "eight": "8", "nine": "9",
}
_FORMATTING = re.compile(r"\n|^\s*(?:[-*•]|\d+[.)])\s|\*\*|#{1,6}\s", re.MULTILINE)


def _digits(text: str) -> str:
    """Every digit said in `text`, in order, whether written as a figure or as a word."""
    return "".join(_DIGIT_WORDS.get(token, re.sub(r"\D", "", token))
                   for token in fold(text).split())


def _facts_missing(
    scenario: Scenario, record: CallRecord, kb: KnowledgeBase, judgement: Judgement | None
) -> list[str]:
    """Facts that can be checked literally: phone numbers, compared digit by digit."""
    said = _digits(" ".join(exchange.answer for exchange in record.exchanges))
    # Said as "seiscientos, quinientos cincuenta y cinco...": the judge transcribes it.
    transcribed = {re.sub(r"\D", "", n) for n in judgement.phone_numbers} if judgement else set()
    literal = kb.facts()
    return [key for key in scenario.expected.must_include_facts
            if key in literal and literal[key] not in said
            and not any(literal[key] in number for number in transcribed)]


def score(
    scenario: Scenario,
    record: CallRecord,
    truth: Truth,
    kb: KnowledgeBase,
    judgement: Judgement | None = None,
) -> Verdict:
    confirmed = truth.client_id(record.confirmed_client_code)
    leaks, breaches = _privacy(scenario, record, truth, kb)
    unsupported, not_heard = _evidence(record)
    action, notes, forbidden, unexpected = _actions(scenario, record, truth)
    facts_missing = _facts_missing(scenario, record, kb, judgement)
    status = {"error": "error", "turn_limit": "unfinished"}.get(record.ended, "scored")
    talk = manners(scenario, record)

    judged: dict = {}
    if judgement is not None:
        limit = scenario.expected.identity.max_questions
        literal = kb.facts()
        if judgement.question_answered is False:
            asked = [k for k in scenario.expected.must_include_facts if k not in literal]
            facts_missing += asked or ["the answer to the caller's question"]
        judged = {
            "identity_questions": judgement.identity_questions,
            "over_asked": limit is not None and judgement.identity_questions > limit,
            "forbidden_claims": [f"promised a transfer: «{f.quote}»"
                                 for f in judgement.transfer_promised],
            "said_wrong": [
                *(f"not in the clinic's information: «{f.quote}» ({f.why})"
                  for f in judgement.unsupported_statements),
                *(f"claimed something that did not happen: «{f.quote}» ({f.why})"
                  for f in judgement.false_action_claims),
            ],
        }
        if judgement.caller_off_script and status == "scored":
            status = "invalid"

    return Verdict(
        scenario_id=scenario.id,
        rep=record.rep,
        category=scenario.category,
        status=status,
        ended=record.ended,
        identity=_identity(scenario, confirmed),
        identified_as=confirmed,
        privacy_leaks=leaks,
        barrier_breaches=breaches,
        unsupported_verifications=unsupported,
        evidence_not_heard=not_heard,
        action=action,
        action_notes=notes,
        forbidden_actions=forbidden,
        unexpected_actions=unexpected,
        facts_missing=facts_missing,
        first_words=[e.first_words if e.first_words is not None else e.seconds
                     for e in record.exchanges],
        answers=[e.seconds for e in record.exchanges],
        words=[len(e.answer.split()) for e in record.exchanges],
        formatted=sum(bool(_FORMATTING.search(e.answer.strip())) for e in record.exchanges),
        asked_several=talk.several,
        asked_who_first=talk.who_before_what,
        asked_before_who=talk.before_who,
        wrong_language=talk.wrong_language,
        **judged,
    )
