"""What a call costs, worked out from what the calls so far were billed."""

import json
from datetime import datetime

from vetdesk.costs import ModelCall, VoiceCall, model_calls, plans, report, voice_calls

TODAY = datetime(2026, 10, 6, 18, 0)


def _voice(name, seconds, dollars, source="sip_trunk", day=6):
    return VoiceCall(name, datetime(2026, 10, day, 12, 0), seconds, int(dollars * 4600),
                     dollars, source, "creator")


def test_the_platform_is_asked_once_for_each_conversation_and_what_it_says_is_kept(tmp_path):
    asked = []
    pages = {"": {"conversations": [{"conversation_id": "a", "status": "done"},
                                    {"conversation_id": "b", "status": "in-progress"}],
                  "has_more": True, "next_cursor": "next"},
             "next": {"conversations": [{"conversation_id": "c", "status": "done"}],
                      "has_more": False}}

    def get(url, headers):
        assert headers == {"xi-api-key": "key"}
        asked.append(url)
        if "/conversations?" in url:
            return pages["next" if "cursor=next" in url else ""]
        name = url.rsplit("/", 1)[1]
        return {"metadata": {"start_time_unix_secs": 1791300000, "call_duration_secs": 60,
                             "cost": 150, "conversation_initiation_source": "sip_trunk",
                             "charging": {"platform_price": 0.03, "llm_price": 0.0,
                                          "tier": "creator"}},
                "transcript": [{"message": f"what was said on {name}"}]}

    cache = tmp_path / "costs" / "elevenlabs.json"
    calls = voice_calls("key", "agent_1", cache, get)
    assert [(call.id, call.seconds, call.credits, call.dollars, call.source, call.tier)
            for call in calls] == [("a", 60, 150, 0.03, "sip_trunk", "creator"),
                                   ("c", 60, 150, 0.03, "sip_trunk", "creator")]
    # Only figures are kept: nothing anybody said on a call.
    assert "what was said" not in cache.read_text(encoding="utf-8")
    assert set(json.loads(cache.read_text(encoding="utf-8"))) == {"a", "c"}
    asked.clear()
    assert len(voice_calls("key", "agent_1", cache, get)) == 2
    assert [url for url in asked if "/conversations/" in url] == []  # nothing asked twice


def test_the_servers_log_gives_the_models_side_and_skips_calls_nobody_spoke_on():
    def get(url, headers):
        assert url == "https://example.test/calls?limit=100000"
        assert headers == {"Authorization": "Bearer admin"}
        return {"calls": [
            {"id": "a", "turns": 4, "requests": 6, "tokens_in": 12000, "tokens_out": 80,
             "dollars": 0.0038, "seconds": 55, "taken_back": 1},
            {"id": "z", "turns": 0, "requests": 0, "tokens_in": 0, "tokens_out": 0,
             "dollars": 0, "seconds": 0, "taken_back": 0}]}

    assert model_calls("https://example.test/", "admin", get) == [
        ModelCall("a", 4, 6, 12000, 80, 0.0038, 55, 1)]


def test_the_report_adds_up_a_call_a_minute_and_a_month():
    voice = [_voice("a", 60, 0.03), _voice("b", 120, 0.06, "react_sdk", day=5),
             _voice("nothing", 0, 0.0)]
    model = [ModelCall("a", 4, 6, 12000, 80, 0.004, 55, 1),
             ModelCall("b", 6, 9, 20000, 120, 0.008, 110),
             ModelCall("typed", 2, 2, 5000, 30, 0.0015, 30)]
    text = report(voice, model, TODAY, model_name="gemini-3.5-flash-lite",
                  calls_per_month=(1000,), phone_per_minute=0.01, fixed_per_month=50,
                  fixed_is="la línea, 44 € al mes.")
    assert "Del 05/10/2026 al 06/10/2026." in text
    assert "| Voz: teléfono | 1 | 1,0 | 0,03 $ | 0,030 $ | 0,0300 $ |" in text
    assert "| **Voz: todas** | 2 | 3,0 | 0,09 $ | 0,045 $ | 0,0300 $ |" in text
    # The model: 0,012 $ over the 3 minutes of the two calls somebody spoke on. The typed
    # one does not count for it: typed to, a minute holds more turns than a spoken one.
    assert ("| **Modelo** (gemini-3.5-flash-lite) | 2 | 3,0 | 0,0120 $ | 0,0060 $ "
            "| 0,0040 $ |") in text
    assert "sale de las llamadas habladas (2); las pruebas escritas (1) no cuentan" in text
    assert "1 de ellos son respuestas que no se llegaron a oír" in text
    assert "que no están en la plataforma de voz (pruebas hechas sin ella): 1." in text
    # A call of a minute and a half, the mean: voice, model and line.
    assert "Una llamada de 1,5 minutos (la media de las hechas hasta hoy):" in text
    assert "| Voz | 0,0450 $ | 0,0300 $ |" in text
    assert "| Línea telefónica | 0,0150 $ | 0,0100 $ |" in text
    assert "| **Total** | **0,0660 $** | **0,0440 $** |" in text
    assert "| 1.000 | 1.500 | 66,00 $ | 50,00 $ | **116,00 $** |" in text
    assert "--phone-per-minute" not in text and "--fixed-per-month" not in text
    # What the monthly figure is made of is written down, and the voice plan's fee is not
    # in it: the credits spent are already counted at its price.
    assert "El fijo es: la línea, 44 € al mes." in text
    assert "La cuota del plan de la plataforma de voz no se suma a los fijos" in text


def test_what_was_not_measured_is_said_to_be_missing_not_guessed():
    text = report([_voice("a", 90, 0.045)], [], TODAY, calls_per_month=(300,), minutes=3)
    assert "Aún no hay ninguna llamada con el gasto del modelo apuntado" in text
    assert "Una llamada de 3,0 minutos (la duración pedida):" in text
    assert "| **Total** | **0,0900 $** | **0,0300 $** |" in text
    assert "| 300 | 900 | **27,00 $** |" in text
    for missing in ("**El modelo**", "**La línea telefónica**", "**Los gastos fijos**"):
        assert missing in text
    assert "Todavía no hay ninguna llamada" in report([], [], TODAY)


def test_with_nothing_but_typed_calls_the_models_price_a_minute_is_said_to_run_high():
    voice = [_voice("a", 120, 0.06), _voice("t", 20, 0.01, "unknown")]
    text = report(voice, [ModelCall("t", 3, 3, 10000, 60, 0.003, 18)], TODAY)
    assert "solo tiene apuntadas pruebas escritas" in text
    assert "| **Modelo** (el del agente) | 1 | 0,3 | 0,0030 $ | 0,0030 $ | 0,0090 $ |" in text


def test_the_month_is_worked_out_at_the_published_prices_too():
    """What the platform charged for the calls and what its price list says are two
    different bills. Which one rules is not known, so the report gives both."""
    tariff = plans("Creator:20:250:0.12, Pro:80:1100:0.08")
    assert [plan.name for plan in tariff] == ["Creator", "Pro"]
    voice = [_voice("a", 60, 0.03), _voice("b", 60, 0.03)]  # a minute each, 0,03 $ a minute
    model = [ModelCall("a", 4, 6, 12000, 80, 0.01, 55), ModelCall("b", 4, 6, 12000, 80, 0.01, 55)]
    text = report(voice, model, TODAY, calls_per_month=(100, 1000, 3000), phone_per_minute=0,
                  fixed_per_month=10, tariff=tariff)
    assert "138 créditos por minuto" in text and "0,0300 $ por minuto" in text
    assert "No se sabe cuál de las dos cuentas manda en la factura" in text
    assert "| Creator | 20,00 $ | 250 | 0,12 $ |" in text
    # 100 minutes fit in the small plan: its fee, the model (0,01 $ a minute) and the fixed.
    assert "| 100 | 100 | Creator | 20,00 $ | **31,00 $** | **14,00 $** |" in text
    # 1.000 minutes: 110 $ on the small plan, 80 $ on the one that covers them.
    assert "| 1.000 | 1.000 | Pro | 80,00 $ | **100,00 $** | **50,00 $** |" in text
    # 3.000: the bigger plan and 1.900 minutes more at 0,08 $.
    assert "| 3.000 | 3.000 | Pro | 232,00 $ | **272,00 $** | **130,00 $** |" in text
    assert "a la tarifa publicada" not in report(voice, model, TODAY)
