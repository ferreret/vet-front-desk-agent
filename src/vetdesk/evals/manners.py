"""How the agent talks, read in code from its own words: no judge and no cost.

A call can pass everything a scenario checks and still be a bad call to be on. Each measure
here is something a person heard on a voice call on 2026-10-05 and the harness had not
shown: three things asked in one breath, the name asked before the caller had said what
they wanted, the name asked last, Spanish answered to Catalan.

These are keyword readings of a transcript, not understanding. They are for counting over
many answers and for pointing at the calls worth reading; one flagged answer proves nothing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..language import spoken_language
from ..legacy.normalize import fold
from ..scenario import Scenario
from .record import CallRecord

# What a clause asks the caller for. A clause that names two of these asks for the first
# in this order: "el nombre de su mascota" asks for the pet, "qué le pasa a su perro" for
# the reason, "qué día le va bien para el gato" for the day.
_ASKS = {
    "when": ("que dia", "que dias", "quin dia", "quins dies", "cuando", "quan", "que hora",
             "quina hora", "manana o", "mati o", "por la manana", "por la tarde", "pel mati",
             "al mati", "a la tarda", "le va bien", "le viene bien", "le iria bien",
             "li va be", "li aniria be", "prefiere", "prefereix", "what day", "which day",
             "what days", "which days", "when", "what time", "morning or", "suit you",
             "suits you", "work for you", "works for you", "prefer"),
    "reason": ("motivo", "motiu", "que le pasa", "que le ocurre", "que li passa", "que tiene",
               "que te", "de que se trata", "de que es tracta", "para que es la",
               "per a que es la", "per que es la", "que necesita", "que necessita", "reason",
               "wrong", "the matter", "visit for", "appointment for", "the problem"),
    "town": ("poblacion", "pueblo", "localidad", "municipio", "donde vive", "poblacio", "poble",
             "localitat", "municipi", "on viu", "town", "city", "village", "where do you live"),
    "phone": ("telefono", "telefon", "numero", "phone", "number"),
    "pet": ("mascota", "animal", "perro", "perra", "gato", "gos", "gossa", "gat", "gata",
            "pet", "pets", "dog", "cat"),
    "name": ("nombre", "apellido", "apellidos", "nom", "cognom", "cognoms", "se llama",
             "te llamas", "es diu", "us dieu", "et dius", "deletre", "lletrej", "name",
             "surname", "surnames", "spell"),
}
_REQUEST = ("digame", "dime", "indiqueme", "necesito", "digui m", "digues", "necessito",
            "tell me", "give me", "may i have", "could you")
_SENTENCE = re.compile(r"[^.!?¿¡]+[.!?]?")
_CLAUSE = re.compile(r"[,;]| y | e | i | and ")
_GREETING = frozenset("""hola buenos buenas dias tardes noches bon bona dia tarda nit hello hi
    good morning afternoon evening""".split())
ABOUT_THE_VISIT = ("pet", "reason", "when")
# Goals that are about the caller's own animals or appointments: the agent has to know who
# is calling, and is told to ask it before anything else about them.
OWN_BUSINESS = ("book", "cancel", "reschedule")


def _has(text: str, phrase: str) -> bool:
    # Whole words, except the two stems (deletrea, deletrear; lletreja, lletrejar).
    padded = f" {text} "
    return f" {phrase}" in padded if phrase in ("deletre", "lletrej") else f" {phrase} " in padded


def asked(answer: str) -> list[str]:
    """The things an answer asks the caller for, in the order it asks them."""
    found: list[str] = []
    for sentence in _SENTENCE.findall(answer):
        plain = fold(sentence)
        if not (sentence.rstrip().endswith("?") or any(_has(plain, r) for r in _REQUEST)):
            continue
        for clause in _CLAUSE.split(sentence):
            clause = fold(clause)
            thing = next((thing for thing, phrases in _ASKS.items()
                          if any(_has(clause, phrase) for phrase in phrases)), None)
            if thing and thing not in found:
                found.append(thing)
    return found


def bare_greeting(line: str) -> bool:
    """Whether a caller's line is a greeting and nothing else: "Hola, bon dia."."""
    words = fold(line).split()
    return bool(words) and set(words) <= _GREETING


@dataclass(frozen=True)
class Manners:
    several: list[int]  # turns whose answer asks for more than one thing
    # Whether a caller who had only said hello was asked who they were. None when the call
    # did not open with a bare greeting.
    who_before_what: bool | None
    # What the agent asked about the visit before asking who was calling. None when the
    # caller's goal needs no identification.
    before_who: list[str] | None
    wrong_language: list[int]  # turns answered in the language the caller is not speaking


def manners(scenario: Scenario, record: CallRecord) -> Manners:
    asks = [asked(exchange.answer) for exchange in record.exchanges]
    opened_bare = bool(record.exchanges) and bare_greeting(record.exchanges[0].said)
    before_who = None
    if scenario.caller.goal.type in OWN_BUSINESS:
        until_who = next((turn for turn, things in enumerate(asks) if "name" in things),
                         len(asks))
        before_who = sorted({thing for things in asks[:until_who] for thing in things
                             if thing in ABOUT_THE_VISIT}, key=ABOUT_THE_VISIT.index)
    return Manners(
        several=[turn for turn, things in enumerate(asks, start=1) if len(things) > 1],
        who_before_what="name" in asks[0] if opened_bare else None,
        before_who=before_who,
        wrong_language=[
            turn for turn, exchange in enumerate(record.exchanges, start=1)
            if spoken_language(exchange.answer)
            not in (None, record.caller_language or scenario.language)
        ],
    )
