"""The appointment book, mirrored in a Google Calendar.

The agent books into `SqliteAgenda`, where nobody at the clinic can look. `MirroredAgenda`
wraps it and keeps a calendar in step, to be looked at: every appointment booked, moved or
cancelled on a call shows up there.

The agenda is the book; the calendar is a picture of it. It is written to after the fact
and off the call's path, on a worker thread: a slow or failing calendar never makes a
caller wait and never fails a booking. It goes one way: what somebody changes by hand in
the calendar changes nothing for the agent. When the server starts, the two are put in
step again: an appointment the calendar lacks is added to it, and one that only the
calendar holds (from before the agenda was kept on disk) is taken into the agenda.

Access is a Google service account, with which the calendar is shared: no person signs in.
"""

from __future__ import annotations

import base64
import json
import logging
import queue
import threading
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from .agenda import Appointment, SqliteAgenda

log = logging.getLogger("vetdesk.calendar")

API = "https://www.googleapis.com/calendar/v3/calendars"
SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
CALENDAR, KEY = "VETDESK_GOOGLE_CALENDAR", "VETDESK_GOOGLE_KEY"
# What marks an event as one of ours, so that nothing else in the calendar is ever touched.
MARK = {"vetdesk": "1"}
_FIELDS = ("appointment_id", "reason", "pet_name", "client_code", "animal_code",
           "contact_name", "contact_phone", "verified")


class Session(Protocol):
    """What is needed of an HTTP session: `requests`' own, with Google's token on it."""

    def request(self, method: str, url: str, **kwargs: Any) -> Any: ...


class GoogleCalendar:
    """The few calls the mirror makes, on one calendar."""

    def __init__(self, calendar_id: str, session: Session, slot_minutes: int,
                 timezone: str = "Europe/Madrid") -> None:
        self._events = f"{API}/{calendar_id}/events"
        self._session, self._slot = session, timedelta(minutes=slot_minutes)
        self._zone = timezone

    def _call(self, method: str, path: str = "", **kwargs: Any) -> dict:
        response = self._session.request(method, self._events + path, timeout=15, **kwargs)
        if response.status_code >= 400:
            raise RuntimeError(f"Google Calendar answered {response.status_code}: "
                               f"{response.text[:200]}")
        return response.json() if response.content else {}

    def _body(self, appointment: Appointment, who: str) -> dict:
        when = appointment.start
        title = f"{appointment.pet_name} · {appointment.reason}"
        if not appointment.verified:
            title += " (sin verificar)"
        private = {name: json.dumps(getattr(appointment, name)) for name in _FIELDS}
        return {
            "summary": title,
            "description": f"{who}\nCita {appointment.appointment_id}, tomada por teléfono "
                           "por el asistente.",
            "start": {"dateTime": when.isoformat(), "timeZone": self._zone},
            "end": {"dateTime": (when + self._slot).isoformat(), "timeZone": self._zone},
            "extendedProperties": {"private": {**MARK, **private}},
        }

    def add(self, appointment: Appointment, who: str) -> str:
        return self._call("POST", json=self._body(appointment, who))["id"]

    def change(self, event_id: str, appointment: Appointment, who: str) -> None:
        self._call("PATCH", f"/{event_id}", json=self._body(appointment, who))

    def remove(self, event_id: str) -> None:
        self._call("DELETE", f"/{event_id}")

    def upcoming(self, now: datetime) -> list[tuple[str, Appointment]]:
        """Our appointments still to come, as (event id, appointment)."""
        since = now.replace(tzinfo=ZoneInfo(self._zone)).isoformat()
        params = {"privateExtendedProperty": "vetdesk=1", "timeMin": since,
                  "singleEvents": "true", "maxResults": "250"}
        found = []
        for event in self._call("GET", params=params).get("items", []):
            private = event.get("extendedProperties", {}).get("private", {})
            try:
                fields = {name: json.loads(private[name]) for name in _FIELDS}
                start = datetime.fromisoformat(event["start"]["dateTime"])
                start = start.astimezone(ZoneInfo(self._zone)).replace(tzinfo=None)
            except (KeyError, ValueError):
                log.info("an event marked as ours could not be read back: %s", event.get("id"))
                continue
            found.append((event["id"], Appointment(start=start, **fields)))
        return found


class MirroredAgenda:
    """An agenda whose appointments are also kept in a calendar. See the module's text."""

    def __init__(self, agenda: SqliteAgenda, calendar: GoogleCalendar,
                 who: Callable[[Appointment], str] = lambda appointment: "") -> None:
        self._agenda, self._calendar, self._who = agenda, calendar, who
        self._event: dict[str, str] = {}  # appointment id -> the calendar's event id
        self._jobs: queue.Queue[Callable[[], None]] = queue.Queue()
        threading.Thread(target=self._work, daemon=True, name="calendar").start()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._agenda, name)  # everything that only reads

    def follow(self, kb: Any) -> None:
        self._agenda.follow(kb)
        self._calendar._slot = timedelta(minutes=kb.appointments.slot_minutes)

    def restore(self, now: datetime) -> tuple[int, int]:
        """Put agenda and calendar in step for the appointments still to come.

        How many the agenda took from the calendar, and how many the calendar was missing.
        """
        taken = 0
        for event_id, appointment in self._calendar.upcoming(now):
            self._event[appointment.appointment_id] = event_id
            if self._agenda.get(appointment.appointment_id) is None:
                self._agenda.add(appointment)
                taken += 1
        missing = [a for a in self._agenda.all()
                   if a.status == "booked" and a.start > now
                   and a.appointment_id not in self._event]
        for appointment in missing:
            self._jobs.put(lambda a=appointment: self._event.__setitem__(
                a.appointment_id, self._calendar.add(a, self._who(a))))
        return taken, len(missing)

    def _work(self) -> None:
        while True:
            job = self._jobs.get()
            try:
                job()
            except Exception as error:  # the calendar is a mirror: it must not hurt a call
                log.warning("the calendar was not updated: %s", error)
            finally:
                self._jobs.task_done()

    def wait(self) -> None:
        """Until the calendar has caught up: for tests, and before the server stops."""
        self._jobs.join()

    def book(self, *args: Any, **kwargs: Any) -> Appointment:
        appointment = self._agenda.book(*args, **kwargs)

        def add() -> None:
            self._event[appointment.appointment_id] = self._calendar.add(
                appointment, self._who(appointment))

        self._jobs.put(add)
        return appointment

    def cancel(self, appointment_id: str) -> Appointment:
        appointment = self._agenda.cancel(appointment_id)

        def remove() -> None:
            if event_id := self._event.pop(appointment_id, None):
                self._calendar.remove(event_id)

        self._jobs.put(remove)
        return appointment

    def reschedule(self, appointment_id: str, new_start: datetime) -> Appointment:
        appointment = self._agenda.reschedule(appointment_id, new_start)

        def change() -> None:
            if event_id := self._event.get(appointment_id):
                self._calendar.change(event_id, appointment, self._who(appointment))

        self._jobs.put(change)
        return appointment


def session_from(key: str) -> Session:
    """A session that signs its requests as the service account whose key this is.

    `key` is the account's JSON key, as it is or in base64: a server's settings take one
    line more gladly than a file of twelve.
    """
    from google.auth.transport.requests import AuthorizedSession
    from google.oauth2 import service_account

    text = key.strip()
    if not text.startswith("{"):
        text = base64.b64decode(text).decode()
    credentials = service_account.Credentials.from_service_account_info(
        json.loads(text), scopes=SCOPES)
    return AuthorizedSession(credentials)
