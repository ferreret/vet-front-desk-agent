"""Compare what a caller said with what is on file, and grade how well it matches.

Two situations are graded differently:

* Heard: the text came from speech recognition. Sound-alike spellings are accepted, and
  near-misses are kept as weak matches that must be confirmed before they count.
* Spelled: the caller confirmed or spelled it. Only the written form counts, with room
  for one typing mistake in a surname on file.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache

from ..legacy.models import PersonName
from ..legacy.normalize import fold, osa_distance
from ..names import given_name_forms
from .phonetics import phonetic_key, sounds_close

# Grades, from best to worst.
EXACT, SOUNDS_SAME, SIMILAR, NO_MATCH = 3, 2, 1, 0
TYPO = SOUNDS_SAME  # spelled names: same written form but for one keystroke
GRADE_NAMES = {EXACT: "exact", SOUNDS_SAME: "sounds the same", SIMILAR: "similar"}

@dataclass(frozen=True)
class SpokenName:
    given: str
    surname1: str
    surname2: str | None = None


def parse_spoken_name(text: str) -> SpokenName | None:
    """Callers give their name as 'Given Surname [Surname]'. One word is not a name."""
    # Accents and ç are kept: they change how a word sounds.
    words = re.findall(r"[^\W\d_]+", text.lower().replace("·", ""))
    if len(words) < 2:
        return None
    return SpokenName(words[0], words[1], words[2] if len(words) > 2 else None)


def _forms(word: str, is_given: bool) -> tuple[str, ...]:
    return given_name_forms(word) if is_given else (word,)


def one_typo_apart(a: str, b: str) -> bool:
    return max(len(a), len(b)) >= 4 and osa_distance(a, b, 1) == 1


@cache
def heard_grade(heard: str, on_file: str, is_given: bool = False) -> int:
    """Grade a word from speech recognition against a word on file (both lowercase)."""
    best = NO_MATCH
    heard_folded, heard_key = fold(heard), phonetic_key(heard)
    for form in _forms(on_file, is_given):
        if fold(form) == heard_folded:
            return EXACT
        form_key = phonetic_key(form)
        if form_key == heard_key:
            best = max(best, SOUNDS_SAME)
        elif sounds_close(heard_key, form_key) or one_typo_apart(heard_folded, fold(form)):
            best = max(best, SIMILAR)
    return best


@cache
def spelled_grade(spelled: str, on_file: str, is_given: bool = False) -> int:
    """Grade a confirmed word against a word on file (both lowercase)."""
    spelled, on_file_folded = fold(spelled), fold(on_file)
    if spelled in {fold(form) for form in _forms(on_file, is_given)}:
        return EXACT
    # A given name one letter away is usually another person (María and Marta, Joan and
    # Joana), so only surnames get the benefit of a typing mistake on file.
    if not is_given and one_typo_apart(spelled, on_file_folded):
        return TYPO
    return NO_MATCH


def name_grade(said: SpokenName, on_file: PersonName, verified: bool) -> int:
    """How well a caller's name matches a client's stored name.

    A second surname only counts when both sides have one: a record stored with a single
    surname cannot contradict the caller, and a caller who gave one surname is not
    contradicted by the record.
    """
    grade = spelled_grade if verified else heard_grade
    parts = [
        grade(said.given, on_file.given.lower(), True),
        grade(said.surname1, on_file.surname1.lower()),
    ]
    if said.surname2 and on_file.surname2:
        parts.append(grade(said.surname2, on_file.surname2.lower()))
    if min(parts) == NO_MATCH:
        return NO_MATCH
    if verified:
        typos = sum(part == TYPO for part in parts)
        return EXACT if typos == 0 else TYPO if typos == 1 else NO_MATCH
    return min(parts)


def pet_grade(said: str, names_on_file: list[str], verified: bool) -> tuple[int, str | None]:
    """Best match between a pet name and a client's animals: (grade, matched name)."""
    best, matched = NO_MATCH, None
    said = said.lower().strip()
    for name in names_on_file:
        grade = heard_grade(said, name.lower())
        if verified and grade < SOUNDS_SAME:
            # Confirmed, a name that only resembles this one is a different name. One that
            # sounds the same still is this one: a caller who repeats "Kira" is heard as
            # "Quira" both times, and confirming must never make a match worse.
            grade = NO_MATCH
        if grade > best:
            best, matched = grade, name
    return best, matched
