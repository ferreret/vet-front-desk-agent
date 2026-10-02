"""Measure the identity resolver against ground truth, with no LLM involved.

A probe is one call reduced to what identification needs: the number, the name and pet as
speech recognition heard them, and the truth. The runner plays the caller: it hands over
the evidence in order and, when the resolver asks to confirm a name it only half
recognises, answers with what was really said (spelling is reliable; hearing is not).

The verdicts are deliberately asymmetric:

* false_identification: the resolver confirmed somebody who is not the caller. Target: zero.
* unsupported_identification: it confirmed the right person without enough evidence.
* coincidence: it confirmed somebody who is not the caller, and so would a perfect listener
  following the policy: a non-client with the same full name and the same pet name as a
  client. No amount of careful listening avoids these; only more evidence would.
* missed: it failed to confirm a caller it could have confirmed. A safe failure.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace

from ..identity import Evidence, IdentityResolver
from ..scenario import Scenario

OUTCOME_OF_DECISION = {"resolved": "resolved", "not_found": "not_a_client", "ask": "unresolved"}


@dataclass(frozen=True)
class Probe:
    id: str
    category: str
    caller_id: str | None  # who is really calling; None when not a client
    number: str | None
    name_said: str
    name_heard: str
    full_name_said: str | None  # set when the caller first gives a single surname
    full_name_heard: str | None
    pet_said: str | None
    pet_heard: str | None
    town_heard: str | None
    noise: str
    expected_outcome: str  # resolved | unresolved | not_a_client
    expected_client_id: str | None
    max_questions: int | None


@dataclass(frozen=True)
class Result:
    probe: Probe
    outcome: str
    client_id: str | None
    questions: int
    verdict: str

    @property
    def over_asked(self) -> bool:
        limit = self.probe.max_questions
        return limit is not None and self.questions > limit


def verdict_of(confirmed: str | None, caller_id: str | None, policy_confirms: str | None) -> str:
    """Judge an identification: who was confirmed, who is calling, whom the policy confirms."""
    if confirmed is None:
        return "missed" if policy_confirms == caller_id is not None else "correct"
    if confirmed != caller_id:
        return "coincidence" if confirmed == policy_confirms else "false_identification"
    return "correct" if policy_confirms == confirmed else "unsupported_identification"


def probes_from_scenarios(scenarios: list[Scenario]) -> list[Probe]:
    probes = []
    for s in scenarios:
        identity = s.expected.identity
        if identity.outcome == "not_required":
            continue
        name, *fuller = (u for u in s.speech.utterances if u.field == "client_name")
        pet = next((u for u in s.speech.utterances if u.field == "pet_name"), None)
        town = next((u for u in s.speech.utterances if u.field == "town"), None)
        if s.caller.goal.type not in ("book", "cancel", "reschedule") and not s.caller.pets:
            pet = None  # the only pet mentioned belongs to somebody else
        probes.append(Probe(
            id=s.id,
            category=s.category,
            caller_id=s.caller.client_id,
            number=s.call.caller_number,
            name_said=name.said,
            name_heard=name.heard,
            full_name_said=fuller[0].said if fuller else None,
            full_name_heard=fuller[0].heard if fuller else None,
            pet_said=pet.said if pet else None,
            pet_heard=pet.heard if pet else None,
            town_heard=town.heard if town else None,
            noise=s.speech.noise,
            expected_outcome=identity.outcome,
            expected_client_id=identity.client_id,
            max_questions=identity.max_questions,
        ))
    return probes


def run_probe(resolver: IdentityResolver, probe: Probe, client_ids: dict[int, str]) -> Result:
    """Play one call against the resolver. `client_ids` maps legacy codes to truth ids."""
    questions = 0

    full_name_said = probe.full_name_said or probe.name_said
    said_full_name_aloud = False

    def settle(evidence: Evidence):
        nonlocal questions, said_full_name_aloud
        while True:
            resolution = resolver.resolve(evidence)
            wants_name = resolution.ask_for in ("full_name", "confirm_name")
            if wants_name and not evidence.name_verified:
                questions += 1
                held_back = probe.full_name_heard and not said_full_name_aloud
                if resolution.ask_for == "full_name" and held_back:
                    # The caller gave one surname and now says both: it can be misheard too.
                    said_full_name_aloud = True
                    evidence = replace(evidence, client_name=probe.full_name_heard)
                else:
                    # Asked to confirm or spell, a caller gives their complete name.
                    evidence = replace(evidence, client_name=full_name_said, name_verified=True)
            elif resolution.ask_for == "confirm_pet" and not evidence.pet_verified:
                questions += 1
                evidence = replace(evidence, pet_name=probe.pet_said, pet_verified=True)
            elif resolution.ask_for == "town" and not evidence.town and probe.town_heard:
                questions += 1
                evidence = replace(evidence, town=probe.town_heard)
            else:
                return evidence, resolution

    questions += 1
    evidence = Evidence(caller_number=probe.number, client_name=probe.name_heard)
    evidence, resolution = settle(evidence)
    if resolution.ask_for == "pet_name" and probe.pet_heard:
        questions += 1
        evidence, resolution = settle(replace(evidence, pet_name=probe.pet_heard))

    outcome = OUTCOME_OF_DECISION[resolution.decision]
    client_id = client_ids[resolution.client.code] if resolution.client else None
    policy_confirms = probe.expected_client_id if probe.expected_outcome == "resolved" else None
    verdict = verdict_of(client_id, probe.caller_id, policy_confirms)
    return Result(probe, outcome, client_id, questions, verdict)


@dataclass(frozen=True)
class Summary:
    total: int
    verdicts: Counter
    over_asked: int
    identifiable: int  # calls where the truth says the caller can be confirmed
    identified: int
    by_category: dict[str, Counter]

    @property
    def false_identifications(self) -> int:
        return self.verdicts["false_identification"]


def summarize(results: list[Result]) -> Summary:
    by_category: dict[str, Counter] = {}
    for r in results:
        counter = by_category.setdefault(r.probe.category, Counter())
        counter[r.verdict] += 1
        counter["over_asked"] += r.over_asked
        counter["total"] += 1
    identifiable = [
        r for r in results
        if r.probe.expected_outcome == "resolved"
        and r.probe.expected_client_id == r.probe.caller_id
    ]
    return Summary(
        total=len(results),
        verdicts=Counter(r.verdict for r in results),
        over_asked=sum(r.over_asked for r in results),
        identifiable=len(identifiable),
        identified=sum(r.verdict == "correct" for r in identifiable),
        by_category=by_category,
    )


def format_report(title: str, summary: Summary) -> str:
    v = summary.verdicts
    rate = summary.identified / summary.identifiable if summary.identifiable else 1.0
    lines = [
        title,
        f"  calls                        {summary.total}",
        f"  false identifications        {v['false_identification']}   <- must be zero",
        f"  unsupported identifications  {v['unsupported_identification']}   <- must be zero",
        f"  coincidences                 {v['coincidence']}   (same full name and pet as a client)",
        f"  correct outcome              {v['correct']} / {summary.total}",
        f"  identified when possible     {summary.identified} / {summary.identifiable}"
        f"  ({rate:.1%})",
        f"  missed (safe failure)        {v['missed']}",
        f"  asked more than allowed      {summary.over_asked}",
        "",
        f"  {'category':36} calls  correct  missed  false  unsupp.  coincid.  over-asked",
    ]
    for category, c in sorted(summary.by_category.items()):
        lines.append(
            f"  {category:36} {c['total']:5}  {c['correct']:7}  {c['missed']:6}  "
            f"{c['false_identification']:5}  {c['unsupported_identification']:7}  "
            f"{c['coincidence']:8}  {c['over_asked']:10}"
        )
    return "\n".join(lines)
