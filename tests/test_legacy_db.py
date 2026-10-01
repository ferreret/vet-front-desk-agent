"""The legacy database file, and the guard that keeps the generator away from foreign data."""

import sqlite3

import pytest

from vetdesk.synth.legacy_db import ForeignDatabaseError, is_generated_db, write_legacy_db


def test_written_database_matches_the_world(world, tmp_path):
    path = tmp_path / "clinic.db"
    write_legacy_db(world, path)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM Clientes").fetchone()[0] == len(world.clients)
        assert db.execute("SELECT COUNT(*) FROM Animales").fetchone()[0] == len(world.pets)
        tables = db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        assert {t[0] for t in tables} == {"Clientes", "Animales"}


def test_animals_have_no_key_to_their_owner(world, tmp_path):
    path = tmp_path / "clinic.db"
    write_legacy_db(world, path)
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA foreign_key_list(Animales)").fetchall() == []
        columns = [row[1] for row in db.execute("PRAGMA table_info(Animales)")]
        assert "Cliente" in columns
        assert not any("cod" in c.lower() and c != "Codigo" for c in columns)
        # The only way from an animal to its owner is a text match on the name.
        linked = db.execute(
            "SELECT COUNT(*) FROM Animales a JOIN Clientes c ON c.Nombre = a.Cliente"
        ).fetchone()[0]
        assert linked > len(world.pets) * 0.8


def test_own_database_is_recognised_and_overwritten(world, tmp_path):
    path = tmp_path / "clinic.db"
    write_legacy_db(world, path)
    assert is_generated_db(path)
    write_legacy_db(world, path)
    assert is_generated_db(path)
    assert list(tmp_path.iterdir()) == [path]


def test_foreign_sqlite_database_is_never_overwritten(world, tmp_path):
    path = tmp_path / "clinic.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE real_clients (name TEXT)")
        db.execute("INSERT INTO real_clients VALUES ('do not lose me')")
    before = path.read_bytes()
    assert not is_generated_db(path)
    with pytest.raises(ForeignDatabaseError):
        write_legacy_db(world, path)
    assert path.read_bytes() == before


def test_any_other_file_is_never_overwritten(world, tmp_path):
    path = tmp_path / "clinic.db"
    path.write_bytes(b"\x00\x01\x00\x00Standard Jet DB\x00" + b"\x00" * 100)
    with pytest.raises(ForeignDatabaseError):
        write_legacy_db(world, path)
