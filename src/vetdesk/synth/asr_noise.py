"""Simulated speech-recognition errors on proper names.

A recogniser tuned for Spanish maps what it hears to spellings it knows. Catalan names
suffer most: they come back as their Castilian cousins or as phonetic guesses. The rules
are deterministic given the random generator, so scenarios are reproducible.
"""

from __future__ import annotations

import random
import re

from .text import fold, strip_accents

LEVELS = ("none", "light", "heavy")

# Whole-word confusions, keyed by the folded word.
_LEXICAL = {
    "joan": ["Juan", "Yoan"],
    "jaume": ["Jaime", "Chaume"],
    "miquel": ["Miguel", "Mikel"],
    "pere": ["Pedro", "Pera"],
    "josep": ["José", "Yusep"],
    "antoni": ["Antonio"],
    "francesc": ["Francisco", "Fransés"],
    "guillem": ["Guillermo", "Guiyem"],
    "llorenc": ["Lorenzo", "Llorens"],
    "andreu": ["Andrés", "Andrew"],
    "mateu": ["Mateo"],
    "pau": ["Pablo", "Pao"],
    "lluis": ["Luis"],
    "vicenc": ["Vicente", "Bisens"],
    "sebastia": ["Sebastián"],
    "margalida": ["Margarita"],
    "francesca": ["Francisca"],
    "caterina": ["Catalina"],
    "aina": ["Ana", "Aína"],
    "merce": ["Mercedes", "Merced"],
    "neus": ["Nieves", "Neus"],
    "xisca": ["Chisca", "Sisca"],
    "esperanca": ["Esperanza"],
    "puig": ["Puch", "Pucho"],
    "vich": ["Vic", "Bic"],
    "bauza": ["Bausa", "Baúza"],
    "llull": ["Yuy", "Lul"],
    "bosch": ["Bosque", "Bosc"],
    "mir": ["Mira"],
    "quetglas": ["Queglas", "Ketglas"],
    "crespi": ["Crespo"],
    "rossello": ["Roselló", "Rosellón"],
    "xispa": ["Chispa"],
    "lluna": ["Luna", "Yuna"],
    "nuvol": ["Núbol", "Nobel"],
    "thor": ["Tor"],
    "rocky": ["Roqui", "Roki"],
    "coco": ["Koko"],
    "kira": ["Quira", "Kyra"],
    "toby": ["Tobi"],
    "max": ["Mas", "Macs"],
    "jack": ["Yak", "Jak"],
    "xoco": ["Choco"],
    "llamp": ["Yam", "Lamp"],
}

# Phonetic slips applied inside a word when no whole-word confusion is known.
_PHONETIC = [
    (r"ll", "y"),
    (r"v", "b"),
    (r"b", "v"),
    (r"z", "s"),
    (r"ç", "s"),
    (r"c(?=[ei])", "s"),
    (r"^h", ""),
    (r"qu", "k"),
    (r"x", "ch"),
    (r"ny", "ñ"),
    (r"ig$", "ch"),
    (r"j", "y"),
    (r"g(?=[ei])", "j"),
    (r"k", "c"),
    (r"y$", "i"),
    (r"rr", "r"),
    (r"s$", ""),
    (r"r$", ""),
    (r"d$", "t"),
]


def _corrupt_word(word: str, level: str, rng: random.Random) -> str:
    unaccented = strip_accents(word)
    if level == "light" and unaccented != word and rng.random() < 0.6:
        return unaccented
    options = [o for o in _LEXICAL.get(fold(word), []) if o != word]
    if options:
        return rng.choice(options)
    lowered = word.lower()
    applicable = [(p, r) for p, r in _PHONETIC if re.search(p, lowered)]
    if applicable:
        pattern, replacement = rng.choice(applicable)
        return re.sub(pattern, replacement, lowered, count=1).capitalize()
    return unaccented


def corrupt(text: str, level: str, rng: random.Random) -> str:
    """Return what a recogniser would plausibly output when a caller says `text`."""
    if level not in LEVELS:
        raise ValueError(f"unknown noise level: {level}")
    if level == "none":
        return text
    words = text.split()
    order = list(range(len(words)))
    rng.shuffle(order)
    wanted = 1 if level == "light" else 3
    for index in order:
        if wanted == 0:
            break
        changed = _corrupt_word(words[index], level, rng)
        if changed and changed != words[index]:
            words[index] = changed
            wanted -= 1
    if level == "heavy" and len(words) >= 3 and rng.random() < 0.4:
        words.pop()  # the trailing surname gets swallowed
    return " ".join(words)
