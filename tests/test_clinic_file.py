"""The clinic's information, changed while the server runs: checked, or not changed at all."""

import asyncio
from datetime import datetime

import pytest
from aiohttp.test_utils import TestClient, TestServer

from vetdesk.agent import FrontDeskAgent
from vetdesk.kb import bundled_kb_text, load_kb, parse_kb, why_not
from vetdesk.llm import Reply
from vetdesk.llm.scripted import ScriptedClient
from vetdesk.scheduling import SqliteAgenda
from vetdesk.voice.clinic_file import ClinicFile
from vetdesk.voice.endpoint import Desk, Switchboard, build_app

NOW = datetime(2026, 11, 3, 10, 15)  # a Tuesday morning
ADMIN = "an-admin-key"


def _desk(clinic, path, steps=()):
    file = ClinicFile(path)
    agenda = SqliteAgenda(file.kb, lambda: NOW)
    model = ScriptedClient(list(steps))
    desk = Desk(lambda kb: FrontDeskAgent(model, clinic, kb, agenda, lambda: NOW), agenda,
                file.kb, file)
    return desk, agenda, model


def _http(app, *calls):
    async def run():
        async with TestClient(TestServer(app)) as client:
            answers = []
            for method, path, key, body in calls:
                response = await client.request(
                    method, path, data=body,
                    headers={"Authorization": f"Bearer {key}"} if key else {})
                answers.append((response.status, await response.text()))
            return answers

    return asyncio.run(run())


def test_the_file_starts_as_the_one_that_comes_with_the_project(tmp_path):
    path = tmp_path / "state" / "clinic.toml"
    file = ClinicFile(path)
    assert path.read_text(encoding="utf-8") == bundled_kb_text() and file.kb == load_kb()
    path.write_text("this is not a clinic", encoding="utf-8")  # broken by hand
    assert ClinicFile(path).kb == load_kb()  # the phone still answers


def test_a_change_is_checked_and_a_wrong_one_changes_nothing(tmp_path):
    path = tmp_path / "clinic.toml"
    file, text = ClinicFile(path), bundled_kb_text()
    for wrong, why in ((text.replace('phone = "+34600555020"', 'phone = "600 555 020"'),
                        "emergency.phone"),
                       (text.replace("price_from_eur = 10", 'price_from_eur = "barato"'),
                        "price_from_eur"),
                       (text.replace('"ru"]', '"ru", "zz"]'), "clinic.languages"),
                       ("[clinic", "not valid TOML")):
        with pytest.raises(Exception) as caught:
            file.replace(wrong)
        assert why in why_not(caught.value)
        assert path.read_text(encoding="utf-8") == text and file.kb == load_kb()
    dearer = text.replace("price_from_eur = 10", "price_from_eur = 12")
    assert dearer != text
    file.replace(dearer)
    assert path.read_text(encoding="utf-8") == dearer and file.kb == parse_kb(dearer)
    assert (tmp_path / "clinic.toml.previous").read_text(encoding="utf-8") == text
    assert ClinicFile(path).kb == parse_kb(dearer)  # and it is what a restart finds


def test_the_next_call_and_the_agenda_follow_the_change(clinic, tmp_path):
    desk, agenda, model = _desk(clinic, tmp_path / "clinic.toml")
    before = desk.start_call(None)
    assert "Corte de uñas: desde 10 euros" in model.transcript.system
    assert agenda.free_slots(NOW.date(), NOW.date(), limit=100)[0].hour == 11  # an hour ahead
    text = bundled_kb_text().replace("price_from_eur = 10", "price_from_eur = 12") \
        .replace("min_notice_minutes = 60", "min_notice_minutes = 180") \
        .replace('"en", "de", "fr", "it", "ru"]', '"en"]')
    desk.replace(text)
    after = desk.start_call(None)
    assert "Corte de uñas: desde 12 euros" in model.transcript.system
    assert "Spanish, Catalan or English" in model.transcript.system
    assert agenda.free_slots(NOW.date(), NOW.date(), limit=100)[0].hour >= 13
    # A language the clinic no longer lists is not changed to.
    assert after.hears("Guten Morgen.") == "es" and before.hears("Guten Morgen.") == "de"
    assert after.hears("Hello, good morning.") == "en"


def test_the_information_is_read_and_replaced_through_the_server_with_its_own_key(clinic, tmp_path):
    desk, _, model = _desk(clinic, tmp_path / "clinic.toml", [Reply("Dígame.")])
    app = build_app(Switchboard(desk.start_call), "the-platforms-key", desk=desk,
                    admin_key=ADMIN)
    text = bundled_kb_text()
    dearer = text.replace("price_from_eur = 10", "price_from_eur = 12")
    (page, read, no_key, platform, broken, saved, again) = _http(
        app,
        ("GET", "/clinic/edit", None, None),
        ("GET", "/clinic", ADMIN, None),
        ("GET", "/clinic", None, None),
        ("PUT", "/clinic", "the-platforms-key", dearer),  # the voice platform cannot edit
        ("PUT", "/clinic", ADMIN, text.replace("+34600555020", "112")),
        ("PUT", "/clinic", ADMIN, dearer),
        ("GET", "/clinic", ADMIN, None),
    )
    assert page[0] == 200 and "Información de la clínica" in page[1] and ADMIN not in page[1]
    assert read == (200, text)
    assert no_key[0] == platform[0] == 401
    assert broken[0] == 400 and "emergency.phone" in broken[1]
    assert saved[0] == 200 and again == (200, dearer)
    desk.start_call(None)
    assert "desde 12 euros" in model.transcript.system


def test_without_an_admin_key_there_is_nothing_to_edit(clinic, tmp_path):
    desk, _, _ = _desk(clinic, tmp_path / "clinic.toml")
    app = build_app(Switchboard(desk.start_call), "the-platforms-key", desk=desk)
    assert [status for status, _ in _http(app, ("GET", "/clinic", "", None),
                                          ("GET", "/clinic/edit", None, None))] == [404, 404]
