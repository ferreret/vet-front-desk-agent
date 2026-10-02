"""The clinic's knowledge base: one validated source for everything the agent may say.

The 2025 pilot went out with template gaps still in its knowledge base and a placeholder
for the emergency number. Loading this model refuses both: every string is checked for
leftover placeholders, phone numbers must be well formed, and the opening hours must cover
the whole year without gaps or overlaps.
"""

from __future__ import annotations

import re
import tomllib
from datetime import date, datetime, time
from importlib import resources
from pathlib import Path

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
WEEKDAYS_ES = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")

_PHONE = re.compile(r"^\+34[6789]\d{8}$")
_PLACEHOLDER = re.compile(
    r"\{\{|\}\}|\[[^\]]*\]|<[^>]*>|\bTODO\b|\bTBD\b|\bXXX+\b|\bPENDIENTE\b|\bRELLENAR\b|"
    r"\bPOR DEFINIR\b|\blorem\b|\?\?\?",
    re.IGNORECASE,
)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def _no_placeholders(self):
        for name, value in self:
            values = value if isinstance(value, list) else [value]
            for item in values:
                if isinstance(item, str) and (not item.strip() or _PLACEHOLDER.search(item)):
                    raise ValueError(f"{name}: empty or placeholder text: {item!r}")
        return self


def _phone(value: str) -> str:
    if not _PHONE.match(value):
        raise ValueError(f"not a Spanish phone number in E.164: {value!r}")
    return value


class Clinic(_Model):
    name: str
    address: str
    phone: str
    languages: list[str]

    _check_phone = field_validator("phone")(_phone)


class Emergency(_Model):
    phone: str
    description: str

    _check_phone = field_validator("phone")(_phone)


class Appointments(_Model):
    slot_minutes: int
    notes: list[str]

    @field_validator("slot_minutes")
    @classmethod
    def _sensible_slot(cls, value: int) -> int:
        if value not in (10, 15, 20, 30, 45, 60):
            raise ValueError("slot_minutes must be one of 10, 15, 20, 30, 45, 60")
        return value


def _interval(text: str) -> tuple[time, time]:
    start, _, end = text.partition("-")
    opening, closing = time.fromisoformat(start), time.fromisoformat(end)
    if opening >= closing:
        raise ValueError(f"interval ends before it starts: {text!r}")
    return opening, closing


def _month_day(text: str) -> tuple[int, int]:
    parsed = date.fromisoformat(f"2024-{text}")  # a leap year, so 02-29 is accepted
    return parsed.month, parsed.day


class Season(_Model):
    season: str
    starts: str  # MM-DD, inclusive
    ends: str  # MM-DD, inclusive
    monday: list[str]
    tuesday: list[str]
    wednesday: list[str]
    thursday: list[str]
    friday: list[str]
    saturday: list[str]
    sunday: list[str]

    @model_validator(mode="after")
    def _well_formed(self):
        _month_day(self.starts), _month_day(self.ends)
        for day in WEEKDAYS:
            intervals = [_interval(text) for text in getattr(self, day)]
            for (_, closing), (opening, _) in zip(intervals, intervals[1:], strict=False):
                if opening < closing:
                    raise ValueError(f"{self.season} {day}: intervals overlap or are out of order")
        return self

    def covers(self, day: date) -> bool:
        start, end, current = _month_day(self.starts), _month_day(self.ends), (day.month, day.day)
        if start <= end:
            return start <= current <= end
        return current >= start or current <= end  # wraps around New Year

    def intervals(self, day: date) -> list[tuple[time, time]]:
        return [_interval(text) for text in getattr(self, WEEKDAYS[day.weekday()])]


class Service(_Model):
    name: str
    description: str
    price_from_eur: int

    @field_validator("price_from_eur")
    @classmethod
    def _positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("price must be positive")
        return value


class Species(_Model):
    treated: list[str]
    not_treated: list[str]


class Faq(_Model):
    question: str
    answer: str


class KnowledgeBase(_Model):
    clinic: Clinic
    emergency: Emergency
    appointments: Appointments
    hours: list[Season]
    services: list[Service]
    species: Species
    faq: list[Faq]

    @model_validator(mode="after")
    def _complete(self):
        if self.emergency.phone == self.clinic.phone:
            raise ValueError("the emergency phone must be a different line from the clinic's")
        if len(self.services) < 3 or len(self.faq) < 3 or not self.species.treated:
            raise ValueError("services, faq and treated species must not be nearly empty")
        day = date(2024, 1, 1)
        while day.year == 2024:  # every day of a leap year belongs to exactly one season
            seasons = [s.season for s in self.hours if s.covers(day)]
            if len(seasons) != 1:
                covered = seasons or "no season"
                raise ValueError(f"opening hours: {day:%m-%d} is covered by {covered}")
            day = date.fromordinal(day.toordinal() + 1)
        return self

    # --- questions the rest of the system asks -------------------------------------------------

    def season_for(self, day: date) -> Season:
        return next(s for s in self.hours if s.covers(day))

    def opening_intervals(self, day: date) -> list[tuple[time, time]]:
        return self.season_for(day).intervals(day)

    def is_open(self, moment: datetime) -> bool:
        return any(
            opening <= moment.time() < closing
            for opening, closing in self.opening_intervals(moment.date())
        )

    def facts(self) -> dict[str, str]:
        """Facts an answer can be checked against literally. Digits only for phones."""
        return {
            "kb.emergency_phone": self.emergency.phone[3:],
            "kb.clinic_phone": self.clinic.phone[3:],
        }

    def render(self) -> str:
        """The knowledge base as the text handed to the agent."""
        lines = [
            f"Clínica veterinaria {self.clinic.name}. Dirección: {self.clinic.address}.",
            f"Teléfono de la clínica: {spoken_phone(self.clinic.phone)}.",
            "",
            f"URGENCIAS: {spoken_phone(self.emergency.phone)}. {self.emergency.description}",
            "",
            "HORARIO",
        ]
        for season in self.hours:
            lines.append(f"Horario de {season.season} (del {_spoken(season.starts)} "
                         f"al {_spoken(season.ends)}):")
            for day, name in zip(WEEKDAYS, WEEKDAYS_ES, strict=True):
                intervals = getattr(season, day)
                opening = " y ".join(i.replace("-", " a ") for i in intervals) or "cerrado"
                lines.append(f"- {name}: {opening}")
        lines += ["", "CITAS"]
        lines += [f"- {note}" for note in self.appointments.notes]
        lines += ["", "SERVICIOS Y PRECIOS ORIENTATIVOS"]
        lines += [
            f"- {s.name}: desde {s.price_from_eur} euros. {s.description}" for s in self.services
        ]
        lines += [
            "",
            f"Animales que se atienden: {', '.join(self.species.treated)}.",
            f"No se atienden: {', '.join(self.species.not_treated)}.",
            "",
            "PREGUNTAS FRECUENTES",
        ]
        lines += [f"- {f.question} {f.answer}" for f in self.faq]
        return "\n".join(lines)


_MONTHS_ES = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
              "septiembre", "octubre", "noviembre", "diciembre")


def _spoken(month_day: str) -> str:
    month, day = _month_day(month_day)
    return f"{day} de {_MONTHS_ES[month - 1]}"


def spoken_phone(number: str) -> str:
    """A phone number as it is said: without the country prefix, in groups of three.

    Given "+34600555020" to read, a model said "más seis cuatro..." to a caller with an
    emergency in two of three measured calls. It is handed the number ready to say.
    """
    digits = number.removeprefix("+34")
    return " ".join(digits[i:i + 3] for i in range(0, len(digits), 3))


def parse_kb(text: str) -> KnowledgeBase:
    return KnowledgeBase.model_validate(tomllib.loads(text))


def load_kb(path: Path | None = None) -> KnowledgeBase:
    """Load and validate a knowledge base; the bundled Planeta Animal one by default."""
    if path is None:
        text = resources.files("vetdesk.kb").joinpath("planeta_animal.toml").read_text("utf-8")
    else:
        text = Path(path).read_text(encoding="utf-8")
    return parse_kb(text)
