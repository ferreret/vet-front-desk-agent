"""No database file may ever be committed, whatever its name or extension."""

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _tracked_files() -> list[Path]:
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True
        )
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    return [ROOT / name for name in result.stdout.decode().split("\0") if name]


def _looks_like_a_database(path: Path) -> bool:
    with open(path, "rb") as handle:
        head = handle.read(32)
    sqlite = head.startswith(b"SQLite format 3\x00")
    access = head[4:19] in (b"Standard Jet DB", b"Standard ACE DB")
    return sqlite or access


def test_no_database_is_tracked_by_git():
    offenders = [p for p in _tracked_files() if p.is_file() and _looks_like_a_database(p)]
    assert offenders == []


def test_no_env_file_is_tracked_by_git():
    env_files = [p.name for p in _tracked_files() if p.name.startswith(".env")]
    assert env_files in ([], [".env.example"])


def test_gitignore_keeps_databases_and_secrets_out():
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").split()
    for pattern in (".env", ".venv/", "*.mdb", "*.accdb", "*.db", "data/"):
        assert pattern in ignored
