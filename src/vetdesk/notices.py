"""What reception is told about a call, as it happens.

The agent says "reception will call you back" when it takes a message. Until this module
the message went nowhere: it stayed in the server's memory. That is the pilot's old failure
in new clothes, a promise nothing kept. Everything a person at the clinic has to know or
act on is written here as a notice, in Spanish, and the voice server sends it on.

A notice is made in code, from what a tool did, never from what the model said it did.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .kb.model import spoken_phone
from .scheduling import Appointment
from .spoken import say_es


@dataclass(frozen=True)
class Notice:
    kind: str  # booked, moved, cancelled, message, emergency, record
    text: str


def _when(moment: datetime) -> str:
    return say_es(moment)


def _phone(number: str | None) -> str:
    return spoken_phone(number) if number else "número oculto"


def _whose(appointment: Appointment, client_name: str | None) -> str:
    if client_name:
        return f"Cliente: {client_name}."
    return (f"SIN VERIFICAR: dice ser {appointment.contact_name}, teléfono "
            f"{_phone(appointment.contact_phone)}. Comprobar antes de la visita.")


def booked(appointment: Appointment, client_name: str | None) -> Notice:
    return Notice("booked", f"CITA NUEVA\n{appointment.pet_name}, {appointment.reason}\n"
                            f"{_when(appointment.start)}\n{_whose(appointment, client_name)}")


def moved(appointment: Appointment, before: datetime, client_name: str | None) -> Notice:
    return Notice("moved", f"CITA CAMBIADA\n{appointment.pet_name}, {appointment.reason}\n"
                           f"Era el {_when(before)}\nAhora es el {_when(appointment.start)}\n"
                           f"{_whose(appointment, client_name)}")


def cancelled(appointment: Appointment, client_name: str | None) -> Notice:
    return Notice("cancelled", f"CITA ANULADA\n{appointment.pet_name}, {appointment.reason}\n"
                               f"Era el {_when(appointment.start)}\n"
                               f"{_whose(appointment, client_name)}")


def message(text: str, contact_name: str, contact_phone: str | None,
            client_name: str | None) -> Notice:
    who = f"{contact_name} (cliente: {client_name})" if client_name else \
        f"{contact_name} (sin identificar)"
    return Notice("message", f"RECADO: llamar a {who}\nTeléfono: {_phone(contact_phone)}\n"
                             f"«{text}»")


def emergency(now: datetime, caller_number: str | None, said: str) -> Notice:
    return Notice("emergency", f"URGENCIA a las {now:%H:%M}\n"
                               f"Llamaban desde: {_phone(caller_number)}\n«{said}»\n"
                               "Se le ha dado el teléfono de urgencias.")


def record(on_file: str, spelled: str, caller_number: str | None) -> Notice:
    return Notice("record", f"FICHA A REVISAR\nHan llamado desde {_phone(caller_number)}, "
                            f"teléfono de la ficha de «{on_file}», y han deletreado su nombre "
                            f"como «{spelled}».\nPuede ser una errata en la ficha. No se le ha "
                            "identificado: se le ha atendido como a quien no es cliente.")
