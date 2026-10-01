"""Adaptation layer between a practice-management system and the rest of vetdesk."""

from .models import Animal, Client, Clinic, ClinicSource, DataIssue, PersonName
from .sqlite_source import LegacySqliteSource

__all__ = [
    "Animal", "Client", "Clinic", "ClinicSource", "DataIssue", "LegacySqliteSource", "PersonName",
]
