"""The knowledge base: complete, well formed, and impossible to load with gaps in it.

The 2025 pilot shipped with unfilled template slots and a placeholder emergency number.
"""

from datetime import date, datetime, time
from importlib import resources

import pytest
from pydantic import ValidationError

from vetdesk.kb import load_kb, parse_kb


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
    assert kb.emergency.phone in text and kb.clinic.phone in text
    assert all(service.name in text for service in kb.services)
    assert "invierno" in text and "verano" in text and "domingo: cerrado" in text
    assert kb.facts() == {"kb.emergency_phone": "600555020", "kb.clinic_phone": "971555010"}


def test_what_the_scenarios_treat_as_unknown_is_really_absent(kb):
    """The harness expects 'I do not know' for these; the knowledge base must not answer."""
    text = kb.render().lower()
    for topic in ("ibuprofeno", "seguro", "ligamento", "dosis"):
        assert topic not in text
