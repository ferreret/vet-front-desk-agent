"""How a name sounds over the phone, for Spanish and Catalan.

Speech recognition returns a spelling for a sound. Two spellings of the same sound must
compare as equal (Vázquez / Bázquez, Ginard / Jinard, Rocky / Roki), and near-misses must
be recognisable as "probably the same word, ask to confirm".
"""

from __future__ import annotations

import re
import unicodedata
from functools import cache

from ..legacy.normalize import osa_distance


@cache
def phonetic_key(word: str) -> str:
    """A rough sound signature. Capital letters stand for sounds, not for letters."""
    w = word.lower().replace("·", "").replace("ç", "s").replace("ñ", "N")
    w = "".join(c for c in unicodedata.normalize("NFD", w) if unicodedata.category(c) != "Mn")
    w = re.sub(r"[^a-zN]", "", w)
    w = w.replace("ny", "N")
    w = re.sub(r"ig$", "X", w)  # Puig
    w = re.sub(r"ch$", "k", w)  # Vich, Bosch
    w = w.replace("tx", "X").replace("ch", "X")
    w = re.sub(r"^x", "X", w).replace("x", "ks")  # Xisca, but Max
    w = re.sub(r"gu(?=[ei])", "G", w)
    w = re.sub(r"g(?=[ei])", "Y", w).replace("g", "G")
    w = w.replace("j", "Y").replace("ll", "Y")
    w = re.sub(r"y(?=[aeiou])", "Y", w).replace("y", "i")
    w = w.replace("qu", "k").replace("q", "k")
    w = re.sub(r"c(?=[ei])", "s", w).replace("c", "k")
    w = w.replace("z", "s").replace("v", "b").replace("w", "u").replace("h", "")
    w = re.sub(r"d$", "t", w)
    w = re.sub(r"Y$", "i", w)  # Moll / Moy
    return re.sub(r"(.)\1+", r"\1", w)


@cache
def sounds_close(key_a: str, key_b: str) -> bool:
    """One sound apart, or two in a long word: likely the same word, heard badly."""
    longest = max(len(key_a), len(key_b))
    if longest < 3:
        return False
    return osa_distance(key_a, key_b, 2) <= (2 if longest >= 7 else 1)
