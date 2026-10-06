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
    ("", None),
])
def test_the_language_of_a_line(line, language):
    assert spoken_language(line) == language
