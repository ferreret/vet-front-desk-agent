"""The agenda mirrored in a calendar, against a stand-in for Google: no network."""

from datetime import datetime
from types import SimpleNamespace

import pytest

from vetdesk.kb import load_kb
from vetdesk.scheduling import AgendaError, SqliteAgenda
from vetdesk.scheduling.google_calendar import GoogleCalendar, MirroredAgenda

NOW = datetime(2026, 11, 3, 10, 15)
SLOT = datetime(2026, 11, 9, 16, 30)


class _Google:
    """A calendar that remembers its events and every request made to it."""

    def __init__(self, fail=False):
        self.events, self.requests, self.fail = {}, [], fail

    def request(self, method, url, timeout=None, json=None, params=None):
        self.requests.append((method, url, params))
        if self.fail:
            return SimpleNamespace(status_code=503, text="unavailable", content=b"x")
        event_id = url.rsplit("/events", 1)[1].lstrip("/")
        if method == "POST":
            event_id = f"ev{len(self.events) + 1}"
            self.events[event_id] = {"id": event_id, **json}
        elif method == "PATCH":
            self.events[event_id].update(json)
        elif method == "DELETE":
            del self.events[event_id]
            return SimpleNamespace(status_code=204, text="", content=b"")
        body = {"items": list(self.events.values())} if method == "GET" else \
            self.events[event_id]
        return SimpleNamespace(status_code=200, text="", content=b"x", json=lambda: body)


@pytest.fixture(scope="module")
def kb():
    return load_kb()


def _mirrored(kb, google, who=lambda appointment: "Cliente: Canals Company, José"):
    calendar = GoogleCalendar("clinic@example.test", google, kb.appointments.slot_minutes)
    return MirroredAgenda(SqliteAgenda(kb, lambda: NOW), calendar, who)


def test_a_booking_shows_up_in_the_calendar(kb):
    google = _Google()
    agenda = _mirrored(kb, google)
    booked = agenda.book(SLOT, "vacuna", "Luna", client_code=10, animal_code=7)
    agenda.wait()
    (event,) = google.events.values()
    assert event["summary"] == "Luna · vacuna"
    assert event["start"] == {"dateTime": "2026-11-09T16:30:00", "timeZone": "Europe/Madrid"}
    assert event["end"]["dateTime"] == "2026-11-09T17:00:00"  # one slot long
    assert "Cliente: Canals Company, José" in event["description"]
    assert booked.appointment_id in event["description"]
    assert google.requests[0][1].endswith("/calendars/clinic@example.test/events")
    # The agent still asks the agenda, not the calendar.
    assert agenda.get(booked.appointment_id).start == SLOT and not agenda.is_free(SLOT)
    with pytest.raises(AgendaError):
        agenda.book(SLOT, "revisión", "Bruno", client_code=10)


def test_an_unverified_booking_says_so(kb):
    google = _Google()
    agenda = _mirrored(kb, google, lambda a: f"Sin verificar: {a.contact_name}")
    agenda.book(SLOT, "revisión", "Toby", contact_name="Marta Soler", contact_phone="+34600111222")
    agenda.wait()
    (event,) = google.events.values()
    assert event["summary"] == "Toby · revisión (sin verificar)"
    assert "Sin verificar: Marta Soler" in event["description"]


def test_moving_and_cancelling_follow(kb):
    google = _Google()
    agenda = _mirrored(kb, google)
    booked = agenda.book(SLOT, "vacuna", "Luna", client_code=10)
    agenda.reschedule(booked.appointment_id, datetime(2026, 11, 10, 9, 30))
    agenda.wait()
    (event,) = google.events.values()
    assert event["start"]["dateTime"] == "2026-11-10T09:30:00"
    agenda.cancel(booked.appointment_id)
    agenda.wait()
    assert google.events == {}
    assert [method for method, _, _ in google.requests] == ["POST", "PATCH", "DELETE"]


def test_a_restart_reads_the_appointments_back(kb):
    """The agenda lives in memory. What is still to come is in the calendar, and comes back."""
    google = _Google()
    first = _mirrored(kb, google)
    kept = first.book(SLOT, "vacuna", "Luna", client_code=10, animal_code=7)
    loose = first.book(datetime(2026, 11, 10, 9, 30), "revisión", "Toby",
                       contact_name="Marta Soler", contact_phone="+34600111222")
    first.wait()
    google.events["theirs"] = {"id": "theirs", "summary": "Dentist",  # not ours: left alone
                               "start": {"dateTime": "2026-11-09T12:00:00+01:00"}}

    again = _mirrored(kb, google)
    assert again.restore(NOW) == 2
    assert google.requests[-1][2]["privateExtendedProperty"] == "vetdesk=1"
    assert google.requests[-1][2]["timeMin"] == "2026-11-03T10:15:00+01:00"
    back = again.get(kept.appointment_id)
    assert back == kept and not again.is_free(SLOT)
    assert again.get(loose.appointment_id) == loose and not back.contact_name
    assert [a.appointment_id for a in again.for_client(10)] == [kept.appointment_id]
    # New bookings take the next number, and the ones read back can still be cancelled.
    assert again.book(datetime(2026, 11, 11, 9, 30), "uñas", "Bruno",
                      client_code=10).appointment_id == "AP-0003"
    again.cancel(kept.appointment_id)
    again.wait()
    assert "ev1" not in google.events and "theirs" in google.events and len(google.events) == 3


def test_a_calendar_that_fails_never_fails_a_booking(kb, caplog):
    google = _Google(fail=True)
    agenda = _mirrored(kb, google)
    booked = agenda.book(SLOT, "vacuna", "Luna", client_code=10)
    agenda.cancel(booked.appointment_id)
    agenda.wait()
    assert agenda.get(booked.appointment_id).status == "cancelled"
    assert "the calendar was not updated" in caplog.text
