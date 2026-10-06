"""Telling Spanish from Catalan by the words of one line."""

import pytest

from vetdesk.language import spoken_language


@pytest.mark.parametrize(("line", "language"), [
    # What was said on the two calls by voice of 2026-10-05, as the recogniser heard it.
    ("Hola, bona tarda.", "ca"),
    ("M'agradaria, eh, f-- eh, demanar una cita.", "ca"),
    ("¿No parles català?", "ca"),
    ("La visita seria pel meu gos Bobby.", "ca"),
    ("Li fa mal la cama.", "ca"),
    ("Dijous que ve.", "ca"),
    ("Quatre, cinc, sis, set, vuit, nou, zero, u, dos.", "ca"),
    ("No, gràcies.", "ca"),
    ("Hola, me gustaría, eh, concertar una cita.", "es"),
    ("Me llamo Nicolás, le llamo de Barcelona y el nombre de mi mascota es Bobby.", "es"),
    ("Tiene pulgas.", "es"),
    ("Sí, mañana a las nueve y media de la mañana.", "es"),
    ("No, eso es todo. Gracias.", "es"),
    # Lines that tell nothing: the call stays in the language it was in.
    ("Nicolás Barceló Lozano.", None),
    ("Joan Feliu Plana.", None),
    ("655 623 963.", None),
    ("Sí.", None),
    ("Hola.", None),
    ("Bobby.", None),
    # A town said alone, as a caller of few words answers: "del" belongs to both languages,
    # and counted as Spanish it turned a call in Catalan into Spanish.
    ("Pinar del Mar.", None),
    ("Santa Aina del Camp.", None),
    ("Visc a Pinar del Mar.", "ca"),
    ("Vivo en Santa Aina del Camp.", "es"),
    # A name spelled out: "i" and "y" are letters before they are words.
    ("M-A-R-T-A S-O-L-E-R V-I-D-A-L", None),
    ("Y-O-L-A-N-D-A R-E-Y", None),
    ("M A R I A", None),
    ("Joan Feliu i Plana.", None),
    ("El meu gos i el meu gat.", "ca"),
    ("Mi perro y mi gato.", "es"),
    # English, the first of the visitors' languages.
    ("Hello, good morning.", "en"),
    ("I'd like to make an appointment.", "en"),
    ("For my dog.", "en"),
    ("Tomorrow morning, please.", "en"),
    ("Six, five, five, six, two, three.", "en"),
    ("No, thank you.", "en"),
    ("John Smith.", None),
    ("Can Pons.", None),  # a house in Catalan before it is a verb in English
    # German, French, Italian; and Russian, told by its alphabet.
    ("Hallo, guten Morgen.", "de"),
    ("Ich möchte einen Termin für meinen Hund.", "de"),
    ("Bonjour.", "fr"),
    ("Je voudrais prendre rendez-vous pour mon chien.", "fr"),
    ("Buongiorno.", "it"),
    ("Vorrei prenotare un appuntamento per il mio cane.", "it"),
    ("Здравствуйте.", "ru"),
    ("Меня зовут Joan Feliu.", "ru"),
    ("Hans Müller.", None),
    ("Maria Rossi.", None),
    ("Ok.", None),
    ("", None),
])
def test_the_language_of_a_line(line, language):
    assert spoken_language(line) == language


def test_no_word_tells_two_languages():
    """A word in two lists tells neither, and would stop telling the one it was first in."""
    from vetdesk.language import _WORDS

    lists = {language: set(words.split()) for language, words in _WORDS.items()}
    for one in ("en", "de", "fr", "it"):  # Spanish and Catalan share words, taken out in code
        for other in lists:
            assert one == other or not lists[one] & lists[other], (one, other)

