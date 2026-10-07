"""The knowledge base: complete, well formed, and impossible to load with gaps in it.

The 2025 pilot shipped with unfilled template slots and a placeholder emergency number.
"""

from datetime import date, datetime, time
from importlib import resources

import pytest
from pydantic import ValidationError

from vetdesk.kb import bundled_kb_text, load_kb, parse_kb, why_not


@pytest.fixture(scope="module")
def kb():
    return load_kb()


@pytest.fixture(scope="module")
def source():
    return resources.files("vetdesk.kb").joinpath("planeta_animal.toml").read_text("utf-8")


def _edited(source: str, old: str, new: str) -> str:
    assert old in source
    return source.replace(old, new)


def test_the_bundled_knowledge_base_is_valid(kb):
    assert kb.clinic.name == "Planeta Animal"
    assert kb.emergency.phone.startswith("+34") and kb.emergency.phone != kb.clinic.phone
    assert len(kb.services) >= 8 and len(kb.faq) >= 5


def test_opening_hours_change_with_the_season(kb):
    winter_tuesday, summer_tuesday = date(2026, 11, 3), date(2026, 7, 7)
    assert kb.season_for(winter_tuesday).season == "invierno"
    assert kb.opening_intervals(winter_tuesday)[0] == (time(9, 30), time(13, 30))
    assert kb.season_for(summer_tuesday).season == "verano"
    assert kb.opening_intervals(summer_tuesday)[0] == (time(9, 0), time(14, 0))
    assert kb.season_for(date(2026, 12, 31)).season == kb.season_for(date(2027, 1, 1)).season


def test_open_and_closed(kb):
    assert kb.is_open(datetime(2026, 11, 3, 10, 15))
    assert not kb.is_open(datetime(2026, 11, 3, 14, 30))  # midday break
    assert not kb.is_open(datetime(2026, 11, 3, 20, 0))  # closing time itself
    assert kb.is_open(datetime(2026, 11, 7, 12, 0))  # Saturday morning
    assert not kb.is_open(datetime(2026, 11, 7, 17, 0))
    assert kb.opening_intervals(date(2026, 11, 8)) == []  # Sunday


@pytest.mark.parametrize("placeholder", [
    "[TELÉFONO DE URGENCIAS]", "{{descripcion}}", "TODO", "PENDIENTE de confirmar", "XXX",
    "<rellenar>", "", "   ", "???",
])
def test_placeholders_and_blanks_are_rejected(source, placeholder):
    broken = _edited(source, 'address = "Carrer Major, 12, Vallserena"',
                     f'address = "{placeholder}"')
    with pytest.raises(ValidationError):
        parse_kb(broken)


@pytest.mark.parametrize("phone", ["+34123", "971555010", "+34 600 555 020", "[número]", ""])
def test_a_malformed_emergency_number_is_rejected(source, phone):
    with pytest.raises(ValidationError):
        parse_kb(_edited(source, 'phone = "+34600555020"', f'phone = "{phone}"'))


def test_the_emergency_line_cannot_be_the_reception_line(source):
    with pytest.raises(ValidationError):
        parse_kb(_edited(source, 'phone = "+34600555020"', 'phone = "+34971555010"'))


def test_a_gap_in_the_opening_hours_is_rejected(source):
    with pytest.raises(ValidationError, match="06-01"):
        parse_kb(_edited(source, 'starts = "06-01"', 'starts = "06-02"'))


def test_overlapping_seasons_are_rejected(source):
    with pytest.raises(ValidationError):
        parse_kb(_edited(source, 'ends = "05-31"', 'ends = "06-15"'))


def test_malformed_intervals_are_rejected(source):
    for bad in ('["13:30-09:30"]', '["09:30-13:30", "13:00-20:00"]', '["mañanas"]'):
        with pytest.raises((ValidationError, ValueError)):
            parse_kb(_edited(source, 'saturday = ["10:00-13:00"]\nsunday = []\n\n[[hours]]',
                             f"saturday = {bad}\nsunday = []\n\n[[hours]]"))


def test_unknown_fields_are_rejected(source):
    with pytest.raises(ValidationError):
        parse_kb(source + '\n[extra]\nnote = "something"\n')


def test_what_the_agent_reads_carries_the_critical_facts(kb):
    text = kb.render()
    # Phones are written as they are said: no country prefix for a model to misread.
    assert "URGENCIAS: 600 555 020." in text and "Teléfono de la clínica: 971 555 010." in text
    assert "+34" not in text
    assert all(service.name in text for service in kb.services)
    assert "invierno" in text and "verano" in text and "domingo: cerrado" in text
    # Opening hours come in words, ready to be said: no clock time is left for a model to
    # read aloud its own way.
    assert ("- lunes: de las nueve y media de la mañana a la una y media de la tarde y de "
            "las cuatro y media de la tarde a las ocho de la tarde") in text
    assert "- sábado: de las diez de la mañana a la una de la tarde" in text
    assert "09:30" not in text and "16:30" not in text
    assert kb.facts() == {"kb.emergency_phone": "600555020", "kb.clinic_phone": "971555010"}


def test_the_opening_hours_in_force_come_first_and_are_the_ones_to_give(kb):
    """Heard on a call in October: asked for the opening hours, the agent read out the
    winter's and then the summer's. It is told which are today's."""
    from datetime import date

    winter, summer = kb.render(date(2026, 10, 7)), kb.render(date(2026, 7, 7))
    now = "Horario de ahora. Es el que se dice cuando preguntan por el horario, sin decir"
    other = "Del {} al {} el horario es otro, el de {}. No se dice, salvo que pregunten"
    assert now in winter and now in summer
    assert other.format("1 de junio", "30 de septiembre", "verano") in winter
    assert winter.index("Horario de ahora") < winter.index("el horario es otro")
    # The hours in force are the ones that follow that line.
    assert "- lunes: de las nueve de la mañana a las dos" in summer[summer.index(now):][:200]
    assert "- lunes: de las nueve y media" in winter[winter.index(now):][:200]
    assert other.format("1 de octubre", "31 de mayo", "invierno") in summer
    # Both are still there, in words: a caller may ask about another time of year.
    for text in (winter, summer):
        assert "de las nueve y media de la mañana" in text
        assert "de las nueve de la mañana a las dos de la tarde" in text
    assert "Horario de ahora" not in kb.render()  # with no day, neither is put first


def test_what_the_scenarios_treat_as_unknown_is_really_absent(kb):
    """The harness expects 'I do not know' for these; the knowledge base must not answer."""
    text = kb.render().lower()
    for topic in ("ibuprofeno", "seguro", "ligamento", "dosis"):
        assert topic not in text


def test_the_days_the_clinic_is_closed_are_told_and_checked(kb):
    text = kb.render(date(2026, 10, 7))
    assert "Días en que la clínica está cerrada por fiesta" in text
    assert "- lunes 12 de octubre: Fiesta Nacional" in text
    # Only the days still to come: every line is paid for on every answer.
    later = kb.render(date(2026, 12, 9))
    assert "12 de octubre" not in later and "- viernes 25 de diciembre: Navidad" in later
    assert "cerrada por fiesta" not in kb.render(date(2027, 2, 1))
    # A file written before there were any still loads, and one day cannot be there twice.
    text = bundled_kb_text()
    start = text.index("[[closed_days]]")
    without = text[:start] + text[text.index("[[services]]"):]
    assert parse_kb(without).closed_days == []
    twice = text.replace("date = 2026-12-08", "date = 2026-10-12")
    with pytest.raises(ValueError) as error:
        parse_kb(twice)
    assert "closed_days" in why_not(error.value)
