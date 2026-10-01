import sqlite3

from vetdesk.cli import main
from vetdesk.scenario import load_jsonl
from vetdesk.synth.scenarios import PLAN


def test_generate_writes_the_three_files(tmp_path, capsys):
    assert main(["generate", "--seed", "5", "--clients", "150", "--out", str(tmp_path)]) == 0
    assert {p.name for p in tmp_path.iterdir()} == {"clinic.db", "truth.json", "scenarios.jsonl"}
    scenarios = load_jsonl((tmp_path / "scenarios.jsonl").read_text(encoding="utf-8"))
    assert len(scenarios) == sum(count for _, count in PLAN)
    assert "150 clients" in capsys.readouterr().out


def test_scenarios_can_be_listed_and_shown(tmp_path, capsys):
    main(["generate", "--out", str(tmp_path)])
    capsys.readouterr()
    file = str(tmp_path / "scenarios.jsonl")
    assert main(["scenarios", "list", "--file", file, "--category", "agenda"]) == 0
    assert len(capsys.readouterr().out.splitlines()) == 9
    assert main(["scenarios", "show", "S-001", "--file", file]) == 0
    assert '"id": "S-001"' in capsys.readouterr().out
    assert main(["scenarios", "show", "S-999", "--file", file]) == 1


def test_generate_refuses_a_directory_holding_someone_elses_database(tmp_path, capsys):
    foreign = tmp_path / "clinic.db"
    with sqlite3.connect(foreign) as db:
        db.execute("CREATE TABLE real_clients (name TEXT)")
    before = foreign.read_bytes()
    assert main(["generate", "--out", str(tmp_path)]) == 1
    assert foreign.read_bytes() == before
    assert "refusing to overwrite" in capsys.readouterr().err
    assert [p.name for p in tmp_path.iterdir()] == ["clinic.db"]


def test_identity_commands(tmp_path, capsys):
    main(["generate", "--out", str(tmp_path)])
    capsys.readouterr()
    assert main(["identity", "eval", "--data", str(tmp_path), "--strangers", "200"]) == 0
    report = capsys.readouterr().out
    assert "false identifications        0" in report and "SWEEP" in report
    db = str(tmp_path / "clinic.db")
    assert main(["identity", "resolve", "--db", db, "--name", "Nobody Atall Here"]) == 0
    assert "decision: not_found" in capsys.readouterr().out
    assert main(["legacy", "inspect", "--db", db]) == 0
    assert "owner_ambiguous" in capsys.readouterr().out


def test_identity_commands_refuse_a_foreign_database(tmp_path, capsys):
    foreign = tmp_path / "clinic.db"
    with sqlite3.connect(foreign) as db:
        db.execute("CREATE TABLE Clientes (Codigo INTEGER)")
    assert main(["legacy", "inspect", "--db", str(foreign)]) == 1
    assert "refusing to open" in capsys.readouterr().err
