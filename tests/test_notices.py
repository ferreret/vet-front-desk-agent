"""What reception is told about a call: made in code from what a tool did, and sent on."""

import asyncio
import json
from datetime import datetime
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer

from vetdesk.agent import FrontDeskAgent, Toolbox
from vetdesk.kb import load_kb
from vetdesk.llm import Reply, ToolCall
from vetdesk.llm.scripted import ScriptedClient
from vetdesk.scheduling import SqliteAgenda
from vetdesk.voice.endpoint import Switchboard, build_app
from vetdesk.voice.telegram import Telegram

NOW = datetime(2026, 11, 3, 10, 15)
SLOT = "2026-11-09T16:30"


@pytest.fixture(scope="module")
def kb():
    return load_kb()


def _run(toolbox, tool, /, **arguments):
    result = toolbox.run(ToolCall("c", tool, arguments))
    return json.loads(result.content), result.is_error


def _client_on_own_phone(clinic):
    """A client with both surnames whose number is on nobody else's record."""
    for client in clinic.clients.values():
        if client.name and client.name.surname2 and client.phones \
                and len(clinic.clients_by_phone(client.phones[0])) == 1 \
                and clinic.animals_of(client.code):
            return client
    raise AssertionError("no such client")


def test_a_message_reaches_reception(clinic, kb):
    """The agent says "reception will call you back". Until now nobody was told."""
    toolbox = Toolbox(clinic, kb, SqliteAgenda(kb, lambda: NOW), lambda: NOW, "+34600111222")
    toolbox.heard("Quería hablar con alguien de una factura. Me llaman a este mismo número.")
    _run(toolbox, "take_message", message="Quiere hablar de una factura.",
         contact_name="Marta Soler", contact_phone=None)
    (notice,) = toolbox.session.notices
    assert notice.kind == "message"
    assert notice.text == ("RECADO\nLlamar a Marta Soler\nTeléfono: 600 111 222\n"
                           "Sin identificar\n«Quiere hablar de una factura.»")
    # For a chat: an emoji to tell it at a glance, who to call in bold, their words apart.
    assert notice.html == ("📩 <b>RECADO</b>\n☎️ Llamar a <b>Marta Soler</b>\n"
                           "📞 Teléfono: <b>600 111 222</b>\n❔ Sin identificar\n"
                           "<blockquote>Quiere hablar de una factura.</blockquote>")


def test_appointments_booked_moved_and_cancelled_are_told(clinic, kb):
    client = _client_on_own_phone(clinic)
    pet = clinic.animals_of(client.code)[0].name
    toolbox = Toolbox(clinic, kb, SqliteAgenda(kb, lambda: NOW), lambda: NOW, client.phones[0])
    name = f"{client.name.given} {client.name.surname1} {client.name.surname2}"
    toolbox.heard(f"Soy {name}, le toca la vacuna.")
    toolbox.session.offered.update({SLOT: -1, "2026-11-10T09:30": -1})  # offered, and taken
    assert _run(toolbox, "identify_client", name=name)[0]["status"] == "confirmed"
    booked, _ = _run(toolbox, "book_appointment", start=SLOT, reason="vacuna", pet_name=pet,
                     contact_name=None, contact_phone=None)
    _run(toolbox, "list_appointments")
    toolbox.heard("Sí, esa.")
    _run(toolbox, "reschedule_appointment", appointment_id=booked["appointment_id"],
         new_start="2026-11-10T09:30")
    _run(toolbox, "cancel_appointment", appointment_id=booked["appointment_id"])
    new, moved, gone = toolbox.session.notices
    assert (new.kind, moved.kind, gone.kind) == ("booked", "moved", "cancelled")
    assert new.text == (f"CITA NUEVA\n{pet}, vacuna\n"
                        "Lunes 9 de noviembre a las cuatro y media de la tarde\n"
                        f"Cliente: {client.raw_name}")
    assert new.html.startswith(f"🟢 <b>CITA NUEVA</b>\n🐾 <b>{pet}</b>, vacuna\n📅 Lunes 9")
    assert moved.html.startswith("🔄 <b>CITA CAMBIADA</b>") and "◀️ Era el lunes 9" in moved.html
    assert gone.html.startswith("❌ <b>CITA ANULADA</b>")
    assert "Era el lunes 9 de noviembre a las cuatro y media de la tarde" in moved.text
    assert "Ahora es el martes 10 de noviembre a las nueve y media de la mañana" in moved.text
    assert gone.text.startswith("CITA ANULADA") and client.raw_name in gone.text


def test_an_unverified_booking_asks_reception_to_check(clinic, kb):
    toolbox = Toolbox(clinic, kb, SqliteAgenda(kb, lambda: NOW), lambda: NOW, None)
    toolbox.heard("Es para una revisión de mi perro Toby.")
    toolbox.session.offered[SLOT] = -1  # offered, and taken
    _run(toolbox, "book_appointment", start=SLOT, reason="revisión", pet_name="Toby",
         contact_name="Marta Soler", contact_phone="600 11 22 33")
    (notice,) = toolbox.session.notices
    assert notice.text.endswith("SIN VERIFICAR: dice ser Marta Soler\nTeléfono: 600 112 233\n"
                                "Comprobar antes de la visita")
    assert "⚠️ <b>SIN VERIFICAR</b>: dice ser Marta Soler" in notice.html
    # What a tool refused is no news: a booking with a reason nobody gave tells nobody.
    _run(toolbox, "book_appointment", start="2026-11-10T09:30", reason="vacuna",
         pet_name="Toby", contact_name="Marta Soler", contact_phone="600 11 22 33")
    assert len(toolbox.session.notices) == 1


def test_a_record_that_looks_misspelt_is_pointed_out_to_reception_and_to_nobody_else(clinic, kb):
    """A caller spells a name one letter from the record their phone is on. They are not
    identified (a brother is a letter away too), but the clinic can mend the record."""
    client = _client_on_own_phone(clinic)
    given = client.name.given
    swapped = given[0] + given[2] + given[1] + given[3:]  # two letters the wrong way round
    assert swapped != given
    spelled = f"{swapped} {client.name.surname1} {client.name.surname2}"
    toolbox = Toolbox(clinic, kb, SqliteAgenda(kb, lambda: NOW), lambda: NOW, client.phones[0])
    toolbox.heard(" ".join("-".join(word.upper()) for word in spelled.split()))
    result, _ = _run(toolbox, "identify_client", name=spelled, name_spelled=True)
    assert result["status"] == "not_a_client" and toolbox.session.client is None
    assert client.raw_name not in json.dumps(result)  # the model learns nothing from it
    (notice,) = toolbox.session.notices
    assert notice.kind == "record" and f"«{client.raw_name}»" in notice.text
    assert f"«{spelled}»" in notice.text and "No se le ha identificado" in notice.text
    # From another phone the same spelling points at no record.
    other = Toolbox(clinic, kb, SqliteAgenda(kb, lambda: NOW), lambda: NOW, None)
    other.heard(" ".join("-".join(word.upper()) for word in spelled.split()))
    _run(other, "identify_client", name=spelled, name_spelled=True)
    assert other.session.notices == []


def test_an_emergency_is_told_at_once_and_once(clinic, kb):
    night = datetime(2026, 11, 8, 3, 20)
    model = ScriptedClient([
        Reply("Es una urgencia: llame ahora al teléfono de urgencias, 600 555 020."),
        Reply("Sí, el 600 555 020."), Reply("Abrimos a las nueve y media.")])
    agent = FrontDeskAgent(model, clinic, kb, SqliteAgenda(kb, lambda: night), lambda: night)
    call = agent.start_call("+34600111222")
    call.say("¡Mi perro se ha comido una tableta de chocolate!")
    call.say("¿Me repite el número?")
    (notice,) = call.session.notices
    # It says what the code knows, that the number was given, and not that it was an
    # emergency: a caller asking whether there is an emergency service gets the number too.
    assert notice.text == ("TELÉFONO DE URGENCIAS DADO a las 03:20\n"
                           "Llamaban desde: 600 111 222\n"
                           "«¡Mi perro se ha comido una tableta de chocolate!»\n"
                           "Puede ser una urgencia o una pregunta por el servicio.")
    assert notice.html.startswith(
        "🚨 <b>TELÉFONO DE URGENCIAS DADO a las 03:20</b>\n📞 Llamaban desde: <b>600")
    assert notice.html.endswith(
        "</blockquote>\n👀 Puede ser una urgencia o una pregunta por el servicio.")
    quiet = agent.start_call(None)
    quiet.say("¿A qué hora abrís?")
    assert quiet.session.notices == []


def test_notices_are_sent_when_the_turn_is_over(clinic, kb):
    """Through the voice server: the notice of a booking goes out with the turn that made
    it, and is taken off the call so that it goes out once."""
    look = ToolCall("a", "get_availability", {
        "date_from": SLOT[:10], "date_to": SLOT[:10], "part_of_day": "afternoon"})
    book = ToolCall("b", "book_appointment", {
        "start": SLOT, "reason": "revisión", "pet_name": "Toby",
        "contact_name": "Marta Soler", "contact_phone": "600 11 22 33"})
    model = ScriptedClient([Reply("", (look,), "tool_calls"), Reply("¿A las cuatro y media?"),
                            Reply("", (book,), "tool_calls"), Reply("Reservado."),
                            Reply("Adiós.")])
    agent = FrontDeskAgent(model, clinic, kb, SqliteAgenda(kb, lambda: NOW), lambda: NOW)
    told = []
    app = build_app(Switchboard(agent.start_call), "key", tell=told.append)
    system = {"role": "system", "content": "vetdesk-conversation: c1\nvetdesk-caller: "}
    offer = [system, {"role": "user", "content": "Una revisión para mi perro Toby, el lunes."}]
    first = [*offer, {"role": "assistant", "content": "¿A las cuatro y media?"},
             {"role": "user", "content": "Sí, a esa hora."}]
    second = [*first, {"role": "assistant", "content": "Reservado."},
              {"role": "user", "content": "Gracias, adiós."}]

    async def run():
        async with TestClient(TestServer(app)) as client:
            for messages in (offer, first, first, second):
                response = await client.post(
                    "/v1/chat/completions", json={"model": "x", "messages": messages},
                    headers={"Authorization": "Bearer key"})
                await response.text()
            await asyncio.sleep(0.05)

    asyncio.run(run())
    assert len(told) == 1 and told[0].text.startswith("CITA NUEVA\nToby, revisión")


def test_what_a_caller_said_cannot_break_the_formatting(clinic, kb):
    """The caller's words go into a message that is marked up: they are escaped."""
    toolbox = Toolbox(clinic, kb, SqliteAgenda(kb, lambda: NOW), lambda: NOW, "+34600111222")
    toolbox.heard("Que me llamen a este mismo teléfono.")
    _run(toolbox, "take_message", message="Dice que pesa <5 kg & no come",
         contact_name="Marta <Soler>", contact_phone=None)
    (notice,) = toolbox.session.notices
    assert "<blockquote>Dice que pesa &lt;5 kg &amp; no come</blockquote>" in notice.html
    assert "<b>Marta &lt;Soler&gt;</b>" in notice.html and "<Soler>" in notice.text


def test_telegram_gets_the_notice_marked_up_and_a_failure_hurts_nobody(caplog):
    from vetdesk.notices import Notice

    sent, answers = [], [200, 400, 200, 403]

    def post(url, json, timeout):
        sent.append((url, json))
        return SimpleNamespace(status_code=answers[len(sent) - 1])

    telegram = Telegram("123:secret", "-1001", post)
    telegram.send(Notice("message", "RECADO\nLlamar a Marta", "📩 <b>RECADO</b>\nLlamar a Marta"))
    telegram.send(Notice("emergency", "URGENCIA", "🚨 <b>URGENCIA"))  # mark-up Telegram refuses
    telegram.send(Notice("booked", "CITA NUEVA", "🟢 <b>CITA NUEVA</b>"))
    telegram.wait()
    assert sent[0] == ("https://api.telegram.org/bot123:secret/sendMessage",
                       {"chat_id": "-1001", "text": "📩 <b>RECADO</b>\nLlamar a Marta",
                        "parse_mode": "HTML"})
    # Refused as marked up, it is sent again plain: better plain than not at all.
    assert sent[2][1] == {"chat_id": "-1001", "text": "URGENCIA"}
    assert "a notice was not delivered: Telegram answered 403" in caplog.text
    assert "secret" not in caplog.text  # the bot's key is never written to the log

    def broken(url, json, timeout):
        raise OSError("no network")

    down = Telegram("123:secret", "-1001", broken)
    down.send(Notice("booked", "CITA NUEVA", "🟢 <b>CITA NUEVA</b>"))
    down.wait()
    assert "a notice was not delivered: OSError" in caplog.text
