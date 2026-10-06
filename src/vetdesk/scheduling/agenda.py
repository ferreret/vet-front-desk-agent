"""The clinic's appointment book.

`Agenda` is the interface the agent's tools use. `SqliteAgenda` keeps the appointments in
a SQLite database of its own, apart from the clinic's records, and takes its opening hours
from the knowledge base. In memory it is a fresh book for every test and every simulated
call; given a file, as on the voice server, the appointments outlive a restart. An Office
365 calendar would be another implementation of the same interface.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal, Protocol

from ..dbguard import APPLICATION_ID, ForeignDatabaseError, is_generated_db
from ..kb import KnowledgeBase

PartOfDay = Literal["morning", "afternoon", "any"]
AFTERNOON_STARTS_AT = 14


class AgendaError(Exception):
    """The requested change cannot be made. The message is safe to relay to the caller."""


@dataclass(frozen=True)
class Appointment:
    appointment_id: str
    start: datetime
    reason: str
    pet_name: str
    client_code: int | None  # None for a caller the agent could not confirm
    animal_code: int | None
    contact_name: str | None
    contact_phone: str | None
    verified: bool
    status: str = "booked"


class Agenda(Protocol):
    def free_slots(
        self, date_from: date, date_to: date, part_of_day: PartOfDay = "any", limit: int = 6
    ) -> list[datetime]: ...

    def book(
        self,
        start: datetime,
        reason: str,
        pet_name: str,
        *,
        client_code: int | None = None,
        animal_code: int | None = None,
        contact_name: str | None = None,
        contact_phone: str | None = None,
    ) -> Appointment: ...

    def get(self, appointment_id: str) -> Appointment | None: ...

    def for_client(self, client_code: int) -> list[Appointment]: ...

    def cancel(self, appointment_id: str) -> Appointment: ...

    def reschedule(self, appointment_id: str, new_start: datetime) -> Appointment: ...


_SCHEMA = """
CREATE TABLE IF NOT EXISTS appointments (
    appointment_id TEXT PRIMARY KEY,
    start          TEXT NOT NULL,
    reason         TEXT NOT NULL,
    pet_name       TEXT NOT NULL,
    client_code    INTEGER,
    animal_code    INTEGER,
    contact_name   TEXT,
    contact_phone  TEXT,
    verified       INTEGER NOT NULL,
    status         TEXT NOT NULL
);
"""
_COLUMNS = ("appointment_id, start, reason, pet_name, client_code, animal_code, contact_name, "
            "contact_phone, verified, status")


class SqliteAgenda:
    def __init__(self, kb: KnowledgeBase, now: Callable[[], datetime],
                 path: Path | None = None) -> None:
        self._kb = kb
        self._now = now
        if path is not None and path.exists() and path.stat().st_size \
                and not is_generated_db(path):
            # Not a file of ours: somebody's data. Never opened, never written to.
            raise ForeignDatabaseError(f"{path} is not an agenda this project made")
        # On a voice line each turn runs on a worker thread, one at a time. Every change
        # is written at once: an appointment booked is on disk before the caller hears so.
        self._db = sqlite3.connect(":memory:" if path is None else path,
                                   check_same_thread=False, isolation_level=None)
        self._db.execute(f"PRAGMA application_id = {APPLICATION_ID}")
        self._db.executescript(_SCHEMA)

    # --- reading ----------------------------------------------------------------------------

    def _slots_of(self, day: date) -> list[datetime]:
        step = timedelta(minutes=self._kb.appointments.slot_minutes)
        slots = []
        for opening, closing in self._kb.opening_intervals(day):
            slot = datetime.combine(day, opening)
            while slot + step <= datetime.combine(day, closing):
                slots.append(slot)
                slot += step
        return slots

    def _taken(self) -> set[datetime]:
        rows = self._db.execute("SELECT start FROM appointments WHERE status = 'booked'")
        return {datetime.fromisoformat(row[0]) for row in rows}

    def _in_time(self, start: datetime) -> bool:
        """Whether a caller can still get there: not in the past, and not in three minutes."""
        now = self._now()
        notice = timedelta(minutes=self._kb.appointments.min_notice_minutes)
        return start > now and start >= now + notice

    def is_free(self, start: datetime) -> bool:
        return self._in_time(start) and start in self._slots_of(start.date()) \
            and start not in self._taken()

    def free_slots(
        self, date_from: date, date_to: date, part_of_day: PartOfDay = "any", limit: int = 6
    ) -> list[datetime]:
        taken, found = self._taken(), []
        day = max(date_from, self._now().date())
        while day <= date_to and len(found) < limit:
            for slot in self._slots_of(day):
                afternoon = slot.hour >= AFTERNOON_STARTS_AT
                wanted = part_of_day == "any" or (part_of_day == "afternoon") == afternoon
                if wanted and self._in_time(slot) and slot not in taken \
                        and len(found) < limit:
                    found.append(slot)
            day += timedelta(days=1)
        return found

    def get(self, appointment_id: str) -> Appointment | None:
        row = self._db.execute(
            f"SELECT {_COLUMNS} FROM appointments WHERE appointment_id = ?", (appointment_id,)
        ).fetchone()
        return _appointment(row) if row else None

    def for_client(self, client_code: int) -> list[Appointment]:
        """Upcoming appointments of a confirmed client."""
        rows = self._db.execute(
            f"SELECT {_COLUMNS} FROM appointments WHERE client_code = ? AND status = 'booked' "
            "AND start > ? ORDER BY start",
            (client_code, self._now().isoformat()),
        )
        return [_appointment(row) for row in rows]

    def all(self) -> list[Appointment]:
        rows = self._db.execute(f"SELECT {_COLUMNS} FROM appointments ORDER BY start")
        return [_appointment(row) for row in rows]

    # --- writing ----------------------------------------------------------------------------

    def add(self, appointment: Appointment) -> None:
        """Insert an appointment as it is: used to load what was booked before the call."""
        a = appointment
        self._db.execute(
            f"INSERT INTO appointments ({_COLUMNS}) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (a.appointment_id, a.start.isoformat(), a.reason, a.pet_name, a.client_code,
             a.animal_code, a.contact_name, a.contact_phone, int(a.verified), a.status),
        )

    def _next_id(self) -> str:
        ids = [row[0] for row in self._db.execute("SELECT appointment_id FROM appointments")]
        numbers = [int(i[3:]) for i in ids if i.startswith("AP-") and i[3:].isdigit()]
        return f"AP-{max(numbers, default=0) + 1:04d}"

    def book(
        self,
        start: datetime,
        reason: str,
        pet_name: str,
        *,
        client_code: int | None = None,
        animal_code: int | None = None,
        contact_name: str | None = None,
        contact_phone: str | None = None,
    ) -> Appointment:
        if not self.is_free(start):
            raise AgendaError("that time is not available")
        appointment = Appointment(
            appointment_id=self._next_id(),
            start=start,
            reason=reason,
            pet_name=pet_name,
            client_code=client_code,
            animal_code=animal_code,
            contact_name=contact_name,
            contact_phone=contact_phone,
            verified=client_code is not None,
        )
        self.add(appointment)
        return appointment

    def _booked(self, appointment_id: str) -> Appointment:
        appointment = self.get(appointment_id)
        if appointment is None or appointment.status != "booked":
            raise AgendaError("there is no such appointment")
        return appointment

    def cancel(self, appointment_id: str) -> Appointment:
        self._booked(appointment_id)
        self._db.execute(
            "UPDATE appointments SET status = 'cancelled' WHERE appointment_id = ?",
            (appointment_id,),
        )
        return self.get(appointment_id)

    def reschedule(self, appointment_id: str, new_start: datetime) -> Appointment:
        self._booked(appointment_id)
        if not self.is_free(new_start):
            raise AgendaError("that time is not available")
        self._db.execute(
            "UPDATE appointments SET start = ? WHERE appointment_id = ?",
            (new_start.isoformat(), appointment_id),
        )
        return self.get(appointment_id)


def _appointment(row: tuple) -> Appointment:
    return Appointment(
        appointment_id=row[0],
        start=datetime.fromisoformat(row[1]),
        reason=row[2],
        pet_name=row[3],
        client_code=row[4],
        animal_code=row[5],
        contact_name=row[6],
        contact_phone=row[7],
        verified=bool(row[8]),
        status=row[9],
    )
