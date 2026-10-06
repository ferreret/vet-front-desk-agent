"""Turn what was typed into the legacy system into comparable values.

Nothing here knows how the synthetic data was produced: it only looks at the strings, the
way an adapter for a real installation would have to.
"""

from __future__ import annotations

import re
import unicodedata

from ..names import canonical_given
from .models import PersonName

_SPECIES = {
    "perro": "perro", "canino": "perro", "can": "perro",
    "gato": "gato", "felino": "gato",
    "conejo": "conejo",
    "ave": "ave", "pajaro": "ave",
    "huron": "hurón",
    "cobaya": "cobaya", "cobaia": "cobaya",
    "tortuga": "tortuga",
}


def fold(text: str) -> str:
    """Comparison key: lowercase, no accents, letters and digits only, single spaces."""
    text = text.replace("·", "")
    decomposed = unicodedata.normalize("NFD", text.lower())
    plain = "".join(c for c in decomposed if unicodedata.category(c) != "Mn")
    return " ".join(re.sub(r"[^a-z0-9]+", " ", plain).split())


def fold_any(text: str) -> str:
    """`fold` for text in any alphabet: the names on file are in Latin letters, but a
    caller may be speaking Russian. Lowercase, no accents, letters and digits of any script."""
    decomposed = unicodedata.normalize("NFD", text.replace("·", "").lower())
    plain = "".join(c for c in decomposed if unicodedata.category(c) != "Mn")
    return " ".join(re.sub(r"[\W_]+", " ", plain).split())


def osa_distance(a: str, b: str, limit: int = 2) -> int:
    """Edit distance counting a swap of two neighbouring letters as one edit.

    Returns `limit + 1` as soon as the distance is known to exceed `limit`.
    """
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    previous2: list[int] = []
    previous = list(range(len(b) + 1))
    for i, char_a in enumerate(a, start=1):
        current = [i]
        for j, char_b in enumerate(b, start=1):
            cost = previous[j - 1] + (char_a != char_b)
            cost = min(cost, previous[j] + 1, current[j - 1] + 1)
            if i > 1 and j > 1 and char_a == b[j - 2] and a[i - 2] == char_b:
                cost = min(cost, previous2[j - 2] + 1)
            current.append(cost)
        if min(current) > limit:
            return limit + 1
        previous2, previous = previous, current
    return min(previous[-1], limit + 1)


def parse_phones(raw: str | None) -> tuple[list[str], bool]:
    """Extract Spanish numbers from a free-text phone field, as E.164.

    Returns the numbers and whether the field held digits that could not be read as one.
    """
    numbers: list[str] = []
    unreadable = False
    for part in re.split(r"[/;,]", raw or ""):
        digits = re.sub(r"\D", "", part)
        if digits.startswith("0034"):
            digits = digits[4:]
        elif len(digits) == 11 and digits.startswith("34"):
            digits = digits[2:]
        if len(digits) == 9 and digits[0] in "6789":
            if f"+34{digits}" not in numbers:
                numbers.append(f"+34{digits}")
        elif digits:
            unreadable = True
    return numbers, unreadable


def parse_name(raw: str | None) -> PersonName | None:
    """Read 'Surname1 Surname2, Given', 'Surname1, Given' or 'Given Surname1 Surname2'."""
    raw = " ".join((raw or "").split())
    if "," in raw:
        surnames, _, given = raw.partition(",")
        parts = surnames.split()
        if not parts or not given.strip():
            return None
        surname2 = " ".join(parts[1:]) or None
        return PersonName(given.strip(), parts[0], surname2)
    parts = raw.split()
    if len(parts) < 2:
        return None
    return PersonName(parts[0], parts[1], " ".join(parts[2:]) or None)


def name_key(raw: str | None) -> str:
    """Key used to follow the owner-name link between animals and clients.

    Two spellings of one person's name give the same key: word order, case, accents and
    the Catalan or Castilian form of the given name do not matter. Deliberately so: a
    client's name gets retyped over the years while their animals keep the old spelling,
    so the exact string must not be trusted to tell two namesakes apart.
    """
    name = parse_name(raw)
    if name is None:
        return fold(raw or "")
    parts = [canonical_given(name.given), fold(name.surname1), fold(name.surname2 or "")]
    return "|".join(parts)


def spelling_key(raw: str | None) -> str:
    """Like `name_key` but keeps the given name as typed, to spot one-keystroke typos."""
    name = parse_name(raw)
    if name is None:
        return fold(raw or "")
    return "|".join([fold(name.given), fold(name.surname1), fold(name.surname2 or "")])


def normalize_species(raw: str | None) -> str:
    folded = fold(raw or "")
    return _SPECIES.get(folded, folded)
