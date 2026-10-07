"""The appointment book: free times follow the opening hours, and nothing is double-booked."""

from datetime import date, datetime

import pytest

from vetdesk.kb import load_kb
from vetdesk.scheduling import AgendaError, Appointment, SqliteAgenda

NOW = datetime(2026, 11, 3, 10, 15)  # a Tuesday morning, winter hours
MONDAY, FRIDAY = date(2026, 11, 9), date(2026, 11, 13)


@pytest.fixture
def agenda():
    return SqliteAgenda(load_kb(), lambda: NOW)


def test_free_times_today_leave_time_to_get_there(agenda):
    """Heard on a voice call: a time offered that began three minutes later."""
    slots = agenda.free_slots(NOW.date(), NOW.date(), limit=100)
    assert load_kb().appointments.min_notice_minutes == 60
    assert slots[0] == datetime(2026, 11, 3, 11, 30)  # 10:30 and 11:00 are too soon at 10:15
    assert slots[-1] == datetime(2026, 11, 3, 19, 30)  # last half hour before closing
    assert all(slot.minute in (0, 30) for slot in slots)
    assert not any(datetime(2026, 11, 3, 13, 30) <= s < datetime(2026, 11, 3, 16, 30)
                   for s in slots)


def test_morning_and_afternoon(agenda):
    mornings = agenda.free_slots(MONDAY, FRIDAY, "morning", limit=100)
    afternoons = agenda.free_slots(MONDAY, FRIDAY, "afternoon", limit=100)
    assert mornings and all(slot.hour < 14 for slot in mornings)
    assert afternoons and all(slot.hour >= 16 for slot in afternoons)
    assert len(agenda.free_slots(MONDAY, FRIDAY)) == 6  # a short list to read out


def test_weekends(agenda):
    saturday, sunday = date(2026, 11, 7), date(2026, 11, 8)
    assert {slot.hour for slot in agenda.free_slots(saturday, saturday, limit=100)} == {10, 11, 12}
    assert agenda.free_slots(sunday, sunday) == []


def test_summer_hours():
    summer = SqliteAgenda(load_kb(), lambda: datetime(2026, 7, 1, 8, 0))
    assert summer.free_slots(date(2026, 7, 1), date(2026, 7, 1))[0].hour == 9


def test_booking_takes_the_time(agenda):
    start = datetime(2026, 11, 9, 9, 30)
    booked = agenda.book(start, "vaccination", "Rocky", client_code=12, animal_code=7)
    assert (booked.appointment_id, booked.verified, booked.status) == ("AP-0001", True, "booked")
    assert start not in agenda.free_slots(MONDAY, MONDAY, limit=100)
    with pytest.raises(AgendaError):
        agenda.book(start, "checkup", "Luna", client_code=13)


@pytest.mark.parametrize("start", [
    datetime(2026, 11, 3, 9, 30),  # already past
    datetime(2026, 11, 9, 14, 0),  # midday break
    datetime(2026, 11, 8, 10, 0),  # Sunday
    datetime(2026, 11, 9, 9, 45),  # not on the half hour
    datetime(2026, 11, 9, 20, 0),  # closing time
])
def test_only_real_free_times_can_be_booked(agenda, start):
    with pytest.raises(AgendaError):
        agenda.book(start, "checkup", "Luna", client_code=1)


def test_unconfirmed_callers_get_an_unverified_booking(agenda):
    booked = agenda.book(datetime(2026, 11, 9, 10, 0), "checkup", "Luna",
                         contact_name="Lucía Romero", contact_phone="+34600111222")
    assert (booked.client_code, booked.verified) == (None, False)
    assert booked.contact_phone == "+34600111222"


def test_cancelling_frees_the_time(agenda):
    start = datetime(2026, 11, 9, 9, 30)
    booked = agenda.book(start, "checkup", "Luna", client_code=1)
    assert agenda.cancel(booked.appointment_id).status == "cancelled"
    assert agenda.is_free(start)
    with pytest.raises(AgendaError):
        agenda.cancel(booked.appointment_id)
    with pytest.raises(AgendaError):
        agenda.cancel("AP-9999")


def test_moving_an_appointment(agenda):
    first, second = datetime(2026, 11, 9, 9, 30), datetime(2026, 11, 10, 17, 0)
    booked = agenda.book(first, "checkup", "Luna", client_code=1)
    other = agenda.book(datetime(2026, 11, 10, 17, 30), "checkup", "Coco", client_code=2)
    moved = agenda.reschedule(booked.appointment_id, second)
    assert moved.start == second and agenda.is_free(first)
    with pytest.raises(AgendaError):
        agenda.reschedule(booked.appointment_id, other.start)


def test_a_clients_upcoming_appointments(agenda):
    agenda.add(Appointment("AP-0007", datetime(2026, 11, 2, 10, 0), "checkup", "Luna", 5, None,
                           None, None, True))  # yesterday
    upcoming = agenda.book(datetime(2026, 11, 9, 9, 30), "checkup", "Luna", client_code=5)
    cancelled = agenda.book(datetime(2026, 11, 9, 10, 0), "checkup", "Luna", client_code=5)
    agenda.cancel(cancelled.appointment_id)
    agenda.book(datetime(2026, 11, 9, 10, 30), "checkup", "Coco", client_code=6)
    assert agenda.for_client(5) == [upcoming]
    assert upcoming.appointment_id == "AP-0008"  # numbering continues after what was loaded


def test_a_time_too_soon_cannot_be_booked_or_moved_to(agenda):
    with pytest.raises(AgendaError):
        agenda.book(datetime(2026, 11, 3, 11, 0), "revisión", "Kira")  # in 45 minutes
    first = agenda.book(datetime(2026, 11, 3, 11, 30), "revisión", "Kira")  # in 75
    with pytest.raises(AgendaError):
        agenda.reschedule(first.appointment_id, datetime(2026, 11, 3, 10, 30))


def test_the_notice_is_the_clinics_to_set():
    kb = load_kb()
    none = kb.model_copy(update={"appointments": kb.appointments.model_copy(
        update={"min_notice_minutes": 0})})
    slots = SqliteAgenda(none, lambda: NOW).free_slots(NOW.date(), NOW.date())
    assert slots[0] == datetime(2026, 11, 3, 10, 30)  # still never in the past
    assert "No se dan citas para antes de 60 minutos" in kb.render()
    assert "No se dan citas para antes de" not in none.render()


def test_no_appointment_on_a_day_the_clinic_is_closed():
    """Offered on a call: Monday 12 October, a public holiday. The clinic's file had no
    holidays in it. A closed day has no opening hours, so the agenda has nothing on it."""
    kb = load_kb()
    before = datetime(2026, 10, 7, 18, 0)  # the Wednesday before
    agenda = SqliteAgenda(kb, lambda: before)
    holiday, after = date(2026, 10, 12), date(2026, 10, 13)
    assert kb.closed_on(holiday).name == "Fiesta Nacional" and kb.closed_on(after) is None
    assert kb.opening_intervals(holiday) == [] and not kb.is_open(datetime(2026, 10, 12, 10, 0))
    assert agenda.free_slots(holiday, holiday) == []
    assert agenda.free_slots(holiday, after, limit=1) == [datetime(2026, 10, 13, 9, 30)]
    with pytest.raises(AgendaError):
        agenda.book(datetime(2026, 10, 12, 10, 0), "vacuna", "Luna", client_code=1)
    booked = agenda.book(datetime(2026, 10, 13, 10, 0), "vacuna", "Luna", client_code=1)
    with pytest.raises(AgendaError):
        agenda.reschedule(booked.appointment_id, datetime(2026, 10, 12, 10, 0))
