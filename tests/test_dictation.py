"""A phone number as it is dictated: in figures, or in words in Spanish and Catalan."""

import pytest

from vetdesk.dictation import dictated, in_groups


@pytest.mark.parametrize("said", [
    "609 176 965",
    "Mi teléfono es el 609-17-69-65.",
    "Seis cero nueve, uno siete seis, nueve seis cinco.",
    "Seis cero nueve, diecisiete, sesenta y nueve, sesenta y cinco.",
    "Seiscientos nueve, ciento setenta y seis, novecientos sesenta y cinco.",
    "Sis zero nou, disset, seixanta-nou, seixanta-cinc.",
    "Sis-cents nou, cent setanta-sis, nou-cents seixanta-cinc.",
    "Sis zero nou, u set sis, nou sis cinc.",
])
def test_a_number_is_read_however_it_is_dictated(said):
    assert "609176965" in dictated(said)


def test_what_was_said_is_read_as_said_and_not_mended():
    """The simulated caller of 2026-10-13 dictated ten figures, then eleven: read as such."""
    assert dictated("El sis zero nou, setze, setanta-sis, nou seixanta-cinc.") == ["6091676965"]
    assert dictated("És el sis zero nou, un set sis, set sis, nou sis cinc.")[-1] == "60917676965"


def test_runs_end_at_other_words():
    assert dictated("Quería una cita para mis dos perros, el 609 176 965") == ["1", "2",
                                                                               "609176965"]
    assert dictated("Veintidós, cien, ciento uno, vint-i-u") == ["2210010121"]
    assert dictated("¿A qué hora abrís el sábado?") == []


def test_said_back_in_threes():
    assert in_groups("609176965") == "609 176 965"
    assert in_groups("6091676965") == "609 167 696 5"
