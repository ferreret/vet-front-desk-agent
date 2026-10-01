"""Recognise the databases this project generates, so no other database is ever touched.

The synthetic generator stamps an application id into the SQLite header. Anything without
that stamp is treated as someone else's data: never overwritten, never opened.
"""

from pathlib import Path

APPLICATION_ID = 0x56445431  # "VDT1"
_SQLITE_MAGIC = b"SQLite format 3\x00"


class ForeignDatabaseError(RuntimeError):
    """The path holds a database this project did not generate."""


def is_generated_db(path: Path) -> bool:
    """True only for files written by the generator. Reads the 72-byte header, no data."""
    with open(path, "rb") as handle:
        header = handle.read(72)
    if len(header) < 72 or header[:16] != _SQLITE_MAGIC:
        return False
    return int.from_bytes(header[68:72], "big") == APPLICATION_ID
