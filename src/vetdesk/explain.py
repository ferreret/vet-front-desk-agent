"""Tell a scenario as a story, for people who would rather not read the JSON."""

from __future__ import annotations

from .scenario import ExpectedAction, Goal, Scenario, TraceStep, Window

LANGUAGES = {"es": "Spanish", "ca": "Catalan"}

NUMBER_RELATIONS = {
    "own": "the caller's own number, on file for them alone",
    "household_shared": "on file for several people of the caller's household",
    "own_not_on_file": "the caller's current number; the clinic does not have it",
    "third_party_client": "a phone borrowed from another client",
    "stale_reassigned": "still on a client's record, but it belongs to someone else now",
    "stranger": "not on file",
    "hidden": "no caller ID",
}

EVIDENCE = {
    "caller_number": "The number arrives",
    "client_name": "The caller says their name",
    "pet_name": "The caller says their pet's name",
}

NONE = "nothing"


class Labels:
    """Turns ids into something readable, using truth.json when it is at hand."""

    def __init__(self, truth: dict | None = None) -> None:
        truth = truth or {}
        self._clients = {
            c["client_id"]: f"{c['given_name']} {c['surname1']} {c['surname2']}"
            for c in truth.get("clients", [])
        }
        self._pets = {p["pet_id"]: p["name"] for p in truth.get("pets", [])}

    def client(self, client_id: str) -> str:
        name = self._clients.get(client_id)
        return f"{client_id} ({name})" if name else client_id

    def clients(self, client_ids: list[str]) -> str:
        return ", ".join(self.client(i) for i in client_ids)

    def pet(self, pet_id: str) -> str:
        name = self._pets.get(pet_id)
        return f"{pet_id} ({name})" if name else pet_id


def _window(window: Window) -> str:
    part = "any time of day" if window.part_of_day == "any" else f"{window.part_of_day}s"
    return f"{window.date_from:%a %d %b} to {window.date_to:%a %d %b %Y}, {part}"


def _reason(reason: str | None) -> str:
    return f" ({reason.replace('_', ' ')})" if reason else ""


def _goal(goal: Goal, labels: Labels) -> str:
    pet = goal.pet_name or "a pet"
    if goal.type == "book":
        new = "" if goal.pet_id else ", a pet the clinic has no record of"
        return f"an appointment for {pet}{new}{_reason(goal.reason)}, {_window(goal.window)}"
    if goal.type == "cancel":
        return f"to cancel {pet}'s appointment {goal.appointment_id}"
    if goal.type == "reschedule":
        return f"to move {pet}'s appointment {goal.appointment_id} to {_window(goal.window)}"
    about = f" It concerns {labels.client(goal.about_client_id)}" if goal.about_client_id else ""
    about += f" and their pet {pet}." if about and goal.pet_name else "." if about else ""
    return f"{goal.topic}.{about}" if goal.topic else goal.type


def _step(number: int, step: TraceStep, labels: Labels, repeated: bool) -> list[str]:
    evidence, expect = step.evidence, step.expect
    said = f'"{evidence.said}"'
    if evidence.heard != evidence.said:
        said += f', heard as "{evidence.heard}"'
    if expect.decision == "resolved":
        verdict = f"confirmed: {labels.client(expect.client_id)}"
    elif expect.decision == "not_found":
        verdict = "no client has this name: the caller is not a client"
    elif expect.consistent_with:
        verdict = f"keep asking. Could still be: {labels.clients(expect.consistent_with)}"
    else:
        verdict = "keep asking. Nobody on file matches so far"
    what = "The caller gives both surnames" if repeated else EVIDENCE[evidence.type]
    return [f"  {number}. {what}: {said}", f"     -> {verdict}"]


def _action(action: ExpectedAction, labels: Labels) -> str:
    if action.tool == "take_message":
        text = "take a message for reception"
    elif action.tool == "cancel_appointment":
        text = f"cancel appointment {action.appointment_id}"
    elif action.tool == "reschedule_appointment":
        text = f"move appointment {action.appointment_id} to {_window(action.window)}"
    else:
        who = labels.client(action.client_id) if action.client_id else "an unconfirmed caller"
        pet = f", pet {labels.pet(action.pet_id)}" if action.pet_id else ""
        text = f"book an appointment for {who}{pet}{_reason(action.reason)}, "
        text += _window(action.window)
        if action.unverified:
            text += "; flagged for reception, no client record touched"
    return f"{text} (optional)" if action.optional else text


def _identity(scenario: Scenario, labels: Labels) -> str:
    identity = scenario.expected.identity
    if identity.outcome == "resolved":
        return f"confirmed as {labels.client(identity.client_id)}"
    if identity.outcome == "unresolved":
        return "cannot be confirmed: the agent must not treat the caller as any client"
    if identity.outcome == "not_a_client":
        return "not a client: the agent must not match the caller to anyone on file"
    return "not needed for this call"


def _questions(limit: int | None) -> str:
    if limit is None:
        return "no limit set"
    if limit == 0:
        return "none: asking who is calling is itself the mistake here"
    return f"at most {limit} identity question{'s' if limit > 1 else ''}"


def explain(scenario: Scenario, labels: Labels | None = None) -> str:
    labels = labels or Labels()
    s, caller, expected = scenario, scenario.caller, scenario.expected
    pilot = f"  (guards against pilot failure {s.pilot_failure})" if s.pilot_failure else ""
    number = s.call.caller_number or "hidden"
    full_name = " ".join(n for n in (caller.given_name, caller.surname1, caller.surname2) if n)
    who = f"client {caller.client_id}" if caller.client_id else "not a client"
    pets = ", ".join(f"{p.name} ({p.pet_id or 'not on file'})" for p in caller.pets) or "none"

    lines = [
        f"{s.id}  {s.category}{pilot}",
        s.notes,
        "",
        "THE CALL",
        f"  When:     {s.clock:%A %Y-%m-%d %H:%M}",
        f"  Number:   {number}: {NUMBER_RELATIONS[s.call.number_relation]}",
        f"  Language: {LANGUAGES[s.language]}",
        "",
        "WHO IS REALLY CALLING  (the agent does not know this)",
        f"  {full_name}, {who}",
        f"  Pets:   {pets}",
        f"  Manner: {caller.persona}",
        f"  Goal:   {_goal(caller.goal, labels)}",
    ]
    if caller.says_name != full_name:
        lines.append(f'  Gives their name as "{caller.says_name}"')

    lines += ["", f"WHAT SPEECH RECOGNITION HEARS  (noise: {s.speech.noise})"]
    for utterance in s.speech.utterances:
        kind = "name" if utterance.field == "client_name" else "pet "
        same = "  (heard correctly)" if utterance.heard == utterance.said else ""
        lines.append(f'  {kind}  "{utterance.said}" -> "{utterance.heard}"{same}')

    if s.fixtures.appointments:
        lines += ["", "ALREADY IN THE AGENDA"]
        for a in s.fixtures.appointments:
            lines.append(
                f"  {a.appointment_id}: {labels.client(a.client_id)}, pet {labels.pet(a.pet_id)}, "
                f"{a.start:%a %d %b %H:%M}{_reason(a.reason)}"
            )

    lines += ["", "IDENTIFICATION, STEP BY STEP"]
    if s.identity_trace:
        seen: set[str] = set()
        for number, step in enumerate(s.identity_trace, start=1):
            lines += _step(number, step, labels, step.evidence.type in seen)
            seen.add(step.evidence.type)
    else:
        lines.append("  Not needed: this call can be handled without knowing who is calling.")

    forbidden = [
        f"{f.tool.replace('_', ' ')} {f.appointment_id}" for f in expected.forbidden_actions
    ]
    traps = expected.identity.forbidden_client_ids
    lines += [
        "",
        "WHAT MUST BE TRUE WHEN THE CALL ENDS",
        f"  Identity:       {_identity(s, labels)}",
        f"  Traps:          {labels.clients(traps) or NONE}"
        + ("  <- picking any of these is a false identification" if traps else ""),
        f"  Questions:      {_questions(expected.identity.max_questions)}",
        "  Reveal nothing: "
        + (labels.clients(expected.privacy.must_not_reveal_about) or "no client is at risk here"),
        f"  Must do:        {'; '.join(_action(a, labels) for a in expected.actions) or NONE}",
        f"  Must not do:    {'; '.join(forbidden) or NONE}",
        f"  Must mention:   {', '.join(expected.must_include_facts) or NONE}",
        f"  Must not claim: {', '.join(expected.forbidden_claims) or NONE}",
    ]
    return "\n".join(lines)
