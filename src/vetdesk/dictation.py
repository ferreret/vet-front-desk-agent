"""The figures in what somebody said: a phone number as it is dictated, in figures or in words.

People in Spain dictate a phone number in figures one by one ("seis cero nueve"), in pairs
("diecisiete, sesenta y nueve") or in threes ("seiscientos nueve"), and a speech recogniser
writes it in figures or in words. Both are read here, in Spanish and in Catalan, so that a
number handed to a tool can be checked against what the caller actually said: given one
dictated in ten figures, a model was measured dropping one in silence and passing a number
nobody had said (5 calls of 6).
"""

from __future__ import annotations

import re
import unicodedata

_UNITS = {
    "cero": 0, "uno": 1, "un": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5,
    "seis": 6, "siete": 7, "ocho": 8, "nueve": 9,
    "zero": 0, "u": 1, "dues": 2, "quatre": 4, "cinc": 5, "sis": 6, "set": 7, "vuit": 8,
    "nou": 9,
}
# Numbers that take no units after them: the teens, and Spanish 21 to 29, said as one word.
_WHOLE = {
    "diez": 10, "once": 11, "doce": 12, "trece": 13, "catorce": 14, "quince": 15,
    "dieciseis": 16, "diecisiete": 17, "dieciocho": 18, "diecinueve": 19,
    "veintiuno": 21, "veintiun": 21, "veintiuna": 21, "veintidos": 22, "veintitres": 23,
    "veinticuatro": 24, "veinticinco": 25, "veintiseis": 26, "veintisiete": 27,
    "veintiocho": 28, "veintinueve": 29,
    "deu": 10, "onze": 11, "dotze": 12, "tretze": 13, "catorze": 14, "quinze": 15,
    "setze": 16, "disset": 17, "divuit": 18, "dinou": 19,
}
_TENS = {
    "veinte": 20, "treinta": 30, "cuarenta": 40, "cincuenta": 50, "sesenta": 60,
    "setenta": 70, "ochenta": 80, "noventa": 90,
    "vint": 20, "trenta": 30, "quaranta": 40, "cinquanta": 50, "seixanta": 60,
    "setanta": 70, "vuitanta": 80, "noranta": 90,
}
_HUNDREDS = {
    "cien": 100, "ciento": 100, "doscientos": 200, "doscientas": 200, "trescientos": 300,
    "trescientas": 300, "cuatrocientos": 400, "cuatrocientas": 400, "quinientos": 500,
    "quinientas": 500, "seiscientos": 600, "seiscientas": 600, "setecientos": 700,
    "setecientas": 700, "ochocientos": 800, "ochocientas": 800, "novecientos": 900,
    "novecientas": 900,
    "cent": 100,
}
_TIMES_HUNDRED = {"cents", "centes"}  # Catalan "sis-cents": a unit, then this
_AND = {"y", "i"}
_TOKENS = re.compile(r"\d+|[a-z]+|[,.;:]")


def _plain(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


class _Number:
    """One number said in words: hundreds, then tens, then units, each optional."""

    def __init__(self) -> None:
        self.hundreds: int | None = None
        self.tens: int | None = None
        self.units: int | None = None
        self.whole = False  # a teen or a 21-29 said as one word: nothing joins it

    def figures(self) -> str:
        return str((self.hundreds or 0) + (self.tens or 0) + (self.units or 0))

    def only_hundreds(self) -> bool:
        return self.hundreds is not None and self.tens is None and self.units is None

    def open_tens(self) -> bool:
        return self.tens is not None and not self.whole and self.units is None


def dictated(text: str) -> list[str]:
    """The runs of figures in `text`, each as one string of digits.

    A run is what was said in one go: figures and number words, with commas and "y"/"i"
    between them, up to the first other word. "Mi teléfono es el seis cero nueve,
    diecisiete, sesenta y nueve, sesenta y cinco" is one run: 609176965.
    """
    runs: list[str] = []
    run: list[str] = []
    number: _Number | None = None

    def close() -> None:
        nonlocal number
        if number is not None:
            run.append(number.figures())
            number = None

    def end_run() -> None:
        close()
        if run:
            runs.append("".join(run))
            run.clear()

    for token in _TOKENS.findall(_plain(text).replace("-", " ")):
        if token.isdigit():
            close()
            run.append(token)
        elif token in _UNITS:
            unit = _UNITS[token]
            if number is not None and (number.open_tens() or number.only_hundreds()):
                number.units = unit
            else:
                close()
                number = _Number()
                number.units = unit
        elif token in _TIMES_HUNDRED:
            if number is not None and number.units and number.hundreds is None \
                    and number.tens is None:
                number.hundreds, number.units = number.units * 100, None
            else:
                end_run()
        elif token in _WHOLE:
            if number is not None and number.only_hundreds():
                number.tens, number.whole = _WHOLE[token], True
            else:
                close()
                number = _Number()
                number.tens, number.whole = _WHOLE[token], True
        elif token in _TENS:
            if number is not None and number.only_hundreds():
                number.tens = _TENS[token]
            else:
                close()
                number = _Number()
                number.tens = _TENS[token]
        elif token in _HUNDREDS:
            close()
            number = _Number()
            number.hundreds = _HUNDREDS[token]
        elif token in _AND:
            continue
        elif token in ",.;:":
            close()
        else:
            end_run()
    end_run()
    return runs


def in_groups(digits: str) -> str:
    """Digits as a phone number is said back: in groups of three."""
    return " ".join(digits[i:i + 3] for i in range(0, len(digits), 3))
