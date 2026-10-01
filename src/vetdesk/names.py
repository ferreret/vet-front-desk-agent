"""Knowledge about personal names shared by the adapter and the identity resolver."""

import unicodedata

# Catalan and Castilian forms of the same given name. Old records often hold one form
# while the person uses the other.
EQUIVALENT_GIVEN_NAMES = [
    ("Joan", "Juan"), ("Antoni", "Antonio"), ("Miquel", "Miguel"), ("Jaume", "Jaime"),
    ("Pere", "Pedro"), ("Josep", "José"), ("Francesc", "Francisco"),
    ("Bartomeu", "Bartolomé"), ("Guillem", "Guillermo"), ("Llorenç", "Lorenzo"),
    ("Rafel", "Rafael"), ("Andreu", "Andrés"), ("Mateu", "Mateo"), ("Bernat", "Bernardo"),
    ("Pau", "Pablo"), ("Marc", "Marcos"), ("Jordi", "Jorge"), ("Xavier", "Javier"),
    ("Lluís", "Luis"), ("Carles", "Carlos"), ("Ferran", "Fernando"), ("Enric", "Enrique"),
    ("Vicenç", "Vicente"), ("Sebastià", "Sebastián"), ("Damià", "Damián"),
    ("Martí", "Martín"), ("Tomàs", "Tomás"), ("Margalida", "Margarita"),
    ("Francesca", "Francisca"), ("Antònia", "Antonia"), ("Joana", "Juana"),
    ("Caterina", "Catalina"), ("Aina", "Ana"), ("Mercè", "Mercedes"), ("Neus", "Nieves"),
    ("Esperança", "Esperanza"), ("Carme", "Carmen"), ("Dolors", "Dolores"),
    ("Lluïsa", "Luisa"), ("Júlia", "Julia"), ("Elisabet", "Isabel"),
    ("Apol·lònia", "Apolonia"), ("Núria", "Nuria"), ("Maria", "María"),
]


def _plain(word: str) -> str:
    decomposed = unicodedata.normalize("NFD", word.lower().replace("·", ""))
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")


# plain form -> every form of that name, lowercase, accents kept (they affect the sound)
_FORMS: dict[str, tuple[str, ...]] = {}
for _group in EQUIVALENT_GIVEN_NAMES:
    _lowered = tuple(name.lower() for name in _group)
    for _name in _lowered:
        _FORMS[_plain(_name)] = _lowered


def given_name_forms(given: str) -> tuple[str, ...]:
    """Every form of a given name, including the one passed in."""
    return _FORMS.get(_plain(given), (given.lower(),))


def canonical_given(given: str) -> str:
    """One key per given name, whatever form it is written in."""
    return _plain(given_name_forms(given)[0])
