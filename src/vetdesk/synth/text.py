"""Text helpers shared by the synthetic generator."""

import unicodedata


def strip_accents(text: str) -> str:
    """Drop diacritics the way an old data-entry screen would: ñ survives, the rest is flattened."""
    out = []
    for ch in text.replace("·", ""):
        if ch in "ñÑ":
            out.append(ch)
            continue
        decomposed = unicodedata.normalize("NFD", ch)
        out.append("".join(c for c in decomposed if unicodedata.category(c) != "Mn"))
    return "".join(out)


def fold(text: str) -> str:
    """Comparison key: no accents, lowercase, single spaces."""
    return " ".join(strip_accents(text).lower().replace("ñ", "n").split())
