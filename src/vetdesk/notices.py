"""What reception is told about a call, as it happens.

The agent says "reception will call you back" when it takes a message. Until this module
the message went nowhere: it stayed in the server's memory. That is the pilot's old failure
in new clothes, a promise nothing kept. Everything a person at the clinic has to know or
act on is written here as a notice, in Spanish, and the voice server sends it on.

A notice is made in code, from what a tool did, never from what the model said it did.

Each one is written twice: plainly, and for a chat that shows bold type and emoji, where a
glance at a phone has to tell an emergency from a nail trim. The emoji are the colour: a
chat has no other.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from html import escape

from .kb.model import spoken_phone
from .scheduling import Appointment
from .spoken import say_es


@dataclass(frozen=True)
class Notice:
    kind: str  # booked, moved, cancelled, message, emergency, record
    text: str  # plain
    html: str  # for a chat: bold, emoji, what the caller said set apart


def _notice(kind: str, icon: str, title: str, rows: list[tuple[str, str, str]],
            said: str | None = None, after: tuple[str, str] | None = None) -> Notice:
    """`rows` are (emoji, what it says, the part of it to stress). `said` is the caller's
    own words, set apart; `after` a last line that comes below them."""
    plain = [title] + [text for _, text, _ in rows]
    rich = [f"{icon} <b>{escape(title)}</b>"]
    for emoji, text, stress in rows:
        line = escape(text)
        if stress:
            line = line.replace(escape(stress), f"<b>{escape(stress)}</b>", 1)
        rich.append(f"{emoji} {line}")
    if said:
        plain.append(f"«{said}»")
        rich.append(f"<blockquote>{escape(said)}</blockquote>")
    if after:
        plain.append(after[1])
        rich.append(f"{after[0]} {escape(after[1])}")
    return Notice(kind, "\n".join(plain), "\n".join(rich))


def _when(moment: datetime) -> str:
    return say_es(moment)


def _phone(number: str | None) -> str:
    return spoken_phone(number) if number else "número oculto"


def _whose(appointment: Appointment, client_name: str | None) -> list[tuple[str, str, str]]:
    if client_name:
        return [("👤", f"Cliente: {client_name}", client_name)]
    return [("⚠️", f"SIN VERIFICAR: dice ser {appointment.contact_name}", "SIN VERIFICAR"),
            ("📞", f"Teléfono: {_phone(appointment.contact_phone)}", ""),
            ("🔎", "Comprobar antes de la visita", "")]


def _visit(appointment: Appointment) -> tuple[str, str, str]:
    return ("🐾", f"{appointment.pet_name}, {appointment.reason}", appointment.pet_name)


def booked(appointment: Appointment, client_name: str | None) -> Notice:
    return _notice("booked", "🟢", "CITA NUEVA", [
        _visit(appointment), ("📅", _when(appointment.start).capitalize(), ""),
        *_whose(appointment, client_name)])


def moved(appointment: Appointment, before: datetime, client_name: str | None) -> Notice:
    return _notice("moved", "🔄", "CITA CAMBIADA", [
        _visit(appointment), ("◀️", f"Era el {_when(before)}", ""),
        ("📅", f"Ahora es el {_when(appointment.start)}", _when(appointment.start)),
        *_whose(appointment, client_name)])


def cancelled(appointment: Appointment, client_name: str | None) -> Notice:
    return _notice("cancelled", "❌", "CITA ANULADA", [
        _visit(appointment), ("📅", f"Era el {_when(appointment.start)}", ""),
        *_whose(appointment, client_name)])


def message(text: str, contact_name: str, contact_phone: str | None,
            client_name: str | None) -> Notice:
    who = ("👤", f"Cliente: {client_name}", "") if client_name else \
        ("❔", "Sin identificar", "")
    return _notice("message", "📩", "RECADO", [
        ("☎️", f"Llamar a {contact_name}", contact_name),
        ("📞", f"Teléfono: {_phone(contact_phone)}", _phone(contact_phone)), who], said=text)


def put_through(summary: str, client_name: str | None, caller_number: str | None) -> Notice:
    who = ("👤", f"Cliente: {client_name}", client_name) if client_name else \
        ("❔", "Sin identificar", "")
    return _notice("put_through", "📲", "LLAMADA PASADA A RECEPCIÓN", [
        ("📞", f"Llaman desde: {_phone(caller_number)}", _phone(caller_number)), who],
        said=summary)


def emergency(now: datetime, caller_number: str | None, said: str) -> Notice:
    """The emergency number was given. That is all the code knows: a caller who asked
    whether the clinic has an emergency service was told the number too, and the notice
    called it an emergency. It says what happened and leaves the caller's words to tell."""
    return _notice("emergency", "🚨", f"TELÉFONO DE URGENCIAS DADO a las {now:%H:%M}", [
        ("📞", f"Llamaban desde: {_phone(caller_number)}", _phone(caller_number))],
        said=said, after=("👀", "Puede ser una urgencia o una pregunta por el servicio."))


def record(on_file: str, spelled: str, caller_number: str | None) -> Notice:
    return _notice("record", "📝", "FICHA A REVISAR", [
        ("📞", f"Han llamado desde {_phone(caller_number)}, teléfono de la ficha de «{on_file}»",
         on_file),
        ("🔤", f"Han deletreado su nombre como «{spelled}»", spelled),
        ("💡", "Puede ser una errata en la ficha", ""),
        ("🚫", "No se le ha identificado: se le ha atendido como a quien no es cliente", "")])
