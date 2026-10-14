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

from ..agent.prompt import CANNOT_JUDGE
from ..language import spoken_language
from ..legacy.normalize import fold_any as fold
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
             "suits you", "work for you", "works for you", "prefer",
             "wann", "welcher tag", "welche tage", "passt", "vormittag oder", "uhrzeit",
             "quand", "quel jour", "quels jours", "convient", "matin ou", "quelle heure",
             "quando", "che giorno", "quali giorni", "va bene", "mattina o", "che ora",
             "когда", "какой день", "какие дни", "удобно", "утром или", "во сколько"),
    "reason": ("motivo", "motiu", "que le pasa", "que le ocurre", "que li passa", "que tiene",
               "que te", "de que se trata", "de que es tracta", "para que es la",
               "per a que es la", "per que es la", "que necesita", "que necessita", "reason",
               "wrong", "the matter", "visit for", "appointment for", "the problem",
               "grund", "worum", "was fehlt", "anlass", "motif", "raison", "qu a t",
               "de quoi s agit", "qu est ce qui ne va pas", "motivo della", "cosa succede",
               "che cosa ha", "причина", "что случилось", "что беспокоит", "по какому поводу"),
    "town": ("poblacion", "pueblo", "localidad", "municipio", "donde vive", "poblacio", "poble",
             "localitat", "municipi", "on viu", "town", "city", "village", "where do you live",
             "ort", "stadt", "wohnen sie", "ville", "habitez", "commune", "citta", "paese",
             "dove abita", "город", "посёлок", "поселок", "где вы живёте", "где вы живете"),
    "phone": ("telefono", "telefon", "numero", "phone", "number", "telefonnummer", "nummer",
              "telephone", "телефон", "номер"),
    "pet": ("mascota", "animal", "perro", "perra", "gato", "gos", "gossa", "gat", "gata",
            "pet", "pets", "dog", "cat", "tier", "haustier", "hund", "katze", "chien", "chat",
            "cane", "gatto", "animale", "питомец", "питомца", "животное", "собака", "кошка"),
    "name": ("nombre", "apellido", "apellidos", "nom", "cognom", "cognoms", "se llama",
             "te llamas", "es diu", "us dieu", "et dius", "deletre", "lletrej", "name",
             "surname", "surnames", "spell", "namen", "nachname", "nachnamen", "buchstabieren",
             "prenom", "epeler", "nome", "cognome", "имя", "фамилия", "фамилию", "по буквам"),
}
_REQUEST = ("digame", "dime", "indiqueme", "necesito", "digui m", "digues", "necessito",
            "tell me", "give me", "may i have", "could you", "nennen sie", "sagen sie",
            "dites moi", "donnez moi", "mi dica", "mi dice", "назовите", "скажите",
            "подскажите")
# Compared as the answers are: lowercase and without accents, in any alphabet.
_ASKS = {thing: tuple(fold(phrase) for phrase in phrases) for thing, phrases in _ASKS.items()}
_REQUEST = tuple(fold(phrase) for phrase in _REQUEST)
_SENTENCE = re.compile(r"[^.!?¿¡]+[.!?]?")
_CLAUSE = re.compile(r"[,;]| y | e | i | and | und | et | и ")
_GREETING = frozenset(fold("""hola buenos buenas dias tardes noches bon bona dia tarda nit hello hi
    good morning afternoon evening hallo guten morgen tag abend bonjour bonsoir buongiorno
    buonasera buon pomeriggio salve здравствуйте доброе утро добрый день вечер"""
    ).split())
ABOUT_THE_VISIT = ("pet", "reason", "when")
# Goals that are about the caller's own animals or appointments: the agent has to know who
# is calling, and is told to ask it before anything else about them.
OWN_BUSINESS = ("book", "cancel", "reschedule")
# An appointment, named. A caller who has only said what is wrong with their animal and is
# asked for their name has to be told what for: heard on a call on 2026-10-11, where the
# way from a symptom to anything the clinic could do was never said.
_APPOINTMENT = tuple(fold(word) for word in (
    "cita", "visita", "hora", "appointment", "termin", "rendez", "appuntamento", "запис",
    "приём", "прием"))


# The agent saying whether what happens to an animal is urgent, serious or can wait. It
# does not judge that since 2026-10-14: whoever calls decides, and it offers them the
# emergency number or a note. Its own sentence for that, which says "si es urgente", is
# taken out before looking.
_JUDGED = tuple(fold(phrase) for phrase in (
    "es una urgencia", "no es una urgencia", "es urgente", "no es urgente", "puede esperar",
    "es grave", "no es grave", "no parece grave", "no se preocupe", "deberia ver",
    "deberia verle", "deberia verla", "tiene que verle", "tiene que ver al animal",
    "conviene que un veterinario", "le dare una cita para ello",
    "és una urgència", "no és urgent", "és urgent", "pot esperar", "és greu", "no és greu",
    "no es preocupi", "hauria de veure", "l'hauria de veure",
    "is an emergency", "is urgent", "is not urgent", "isn't urgent", "can wait",
    "is serious", "not serious", "don't worry", "should see a vet", "should be seen",
    "ist ein notfall", "kein notfall", "c'est une urgence", "pas urgent", "è un'urgenza",
    "non è urgente"))
_OWN = tuple(fold(sentence) for sentence in CANNOT_JUDGE.values())


def judged(answer: str) -> bool:
    """Whether an answer says that something is, or is not, urgent or serious."""
    plain = f" {fold(answer)} "
    for own in _OWN:
        plain = plain.replace(f" {own} ", " ")
    return any(f" {phrase} " in plain for phrase in _JUDGED)


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
    # Whether a caller who had described a symptom and asked for nothing was asked who they
    # were with no word of an appointment, then or before. None when the caller's goal is
    # another, or they were never asked.
    who_unexplained: bool | None
    judged: list[int]  # turns whose answer said whether something was urgent or could wait


def manners(scenario: Scenario, record: CallRecord) -> Manners:
    asks = [asked(exchange.answer) for exchange in record.exchanges]
    opened_bare = bool(record.exchanges) and bare_greeting(record.exchanges[0].said)
    before_who = None
    if scenario.caller.goal.type in OWN_BUSINESS:
        until_who = next((turn for turn, things in enumerate(asks) if "name" in things),
                         len(asks))
        before_who = sorted({thing for things in asks[:until_who] for thing in things
                             if thing in ABOUT_THE_VISIT}, key=ABOUT_THE_VISIT.index)
    who_unexplained = None
    if scenario.caller.goal.type == "symptom":
        until = next((turn for turn, things in enumerate(asks) if "name" in things), None)
        if until is not None:
            so_far = " " + fold(" ".join(e.answer for e in record.exchanges[:until + 1]))
            # From the start of a word: "ahora" names no appointment, "citas" does.
            who_unexplained = not any(f" {word}" in so_far for word in _APPOINTMENT)
    return Manners(
        several=[turn for turn, things in enumerate(asks, start=1) if len(things) > 1],
        who_before_what="name" in asks[0] if opened_bare else None,
        before_who=before_who,
        wrong_language=[
            turn for turn, exchange in enumerate(record.exchanges, start=1)
            if spoken_language(exchange.answer)
            not in (None, record.caller_language or scenario.language)
        ],
        who_unexplained=who_unexplained,
        judged=[turn for turn, exchange in enumerate(record.exchanges, start=1)
                if judged(exchange.answer)],
    )
