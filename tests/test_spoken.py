"""Days and times in words: written by code, because a caller acts on them."""

from datetime import datetime

import pytest

from vetdesk.agent.spoken import say_ca, say_es


@pytest.mark.parametrize(("moment", "spanish", "catalan"), [
    ("2026-11-09T09:30", "lunes 9 de noviembre a las nueve y media de la mañana",
     "dilluns 9 de novembre a les nou i mitja del matí"),
    # The time a model got wrong by an hour in Catalan, six calls out of eighty-two.
    ("2026-11-09T16:30", "lunes 9 de noviembre a las cuatro y media de la tarde",
     "dilluns 9 de novembre a les quatre i mitja de la tarda"),
    ("2026-11-10T13:00", "martes 10 de noviembre a la una de la tarde",
     "dimarts 10 de novembre a la una del migdia"),
    ("2026-11-14T12:00", "sábado 14 de noviembre a las doce del mediodía",
     "dissabte 14 de novembre a les dotze del migdia"),
    ("2026-04-01T10:15", "miércoles 1 de abril a las diez y cuarto de la mañana",
     "dimecres 1 d'abril a les deu i quart del matí"),
    ("2026-08-06T20:00", "jueves 6 de agosto a las ocho de la tarde",
     "dijous 6 d'agost a les vuit del vespre"),
    ("2026-10-02T17:45", "viernes 2 de octubre a las cinco y cuarenta y cinco de la tarde",
     "divendres 2 d'octubre a les cinc i quaranta-cinc de la tarda"),
    ("2026-11-08T03:20", "domingo 8 de noviembre a las tres y veinte de la madrugada",
     "diumenge 8 de novembre a les tres i vint de la matinada"),
])
def test_days_and_times_as_they_are_said(moment, spanish, catalan):
    when = datetime.fromisoformat(moment)
    assert say_es(when) == spanish
    assert say_ca(when) == catalan


def test_every_slot_the_agenda_can_offer_has_words(clinic):
    """No time the clinic can book falls outside what the tables cover."""
    from vetdesk.kb import load_kb
    from vetdesk.scheduling import SqliteAgenda

    kb = load_kb()
    now = datetime(2026, 11, 3, 8, 0)
    agenda = SqliteAgenda(kb, lambda: now)
    slots = agenda.free_slots(now.date(), datetime(2026, 11, 9).date(), limit=10_000)
    assert len(slots) > 60
    for slot in slots:
        for said in (say_es(slot), say_ca(slot)):
            assert not any(ch.isdigit() for ch in said.split(" a ", 1)[1]), said
