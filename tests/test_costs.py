"""What a call costs, worked out from what the calls so far were billed."""

import json
from datetime import datetime

from vetdesk.costs import ModelCall, VoiceCall, model_calls, report, voice_calls

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
                  calls_per_month=(1000,), phone_per_minute=0.01, fixed_per_month=50)
    assert "Del 05/10/2026 al 06/10/2026." in text
    assert "| Voz: teléfono | 1 | 1,0 | 0,03 $ | 0,030 $ | 0,0300 $ |" in text
    assert "| **Voz: todas** | 2 | 3,0 | 0,09 $ | 0,045 $ | 0,0300 $ |" in text
    # The model: 0,0135 $ over 3,5 minutes (the typed call's are the server's own count).
    assert ("| **Modelo** (gemini-3.5-flash-lite) | 3 | 3,5 | 0,0135 $ | 0,0045 $ "
            "| 0,0039 $ |") in text
    assert "1 de ellos son respuestas que no se llegaron a oír" in text
    assert "1 llamadas del servidor no están en la plataforma de voz" in text
    # A call of a minute and a half, the mean: voice, model and line.
    assert "Una llamada de 1,5 minutos (la media de las hechas hasta hoy):" in text
    assert "| Voz | 0,0450 $ | 0,0300 $ |" in text
    assert "| Línea telefónica | 0,0150 $ | 0,0100 $ |" in text
    assert "| **Total** | **0,0658 $** | **0,0439 $** |" in text
    assert "| 1.000 | 1.500 | 65,79 $ | 50,00 $ | **115,79 $** |" in text
    assert "--phone-per-minute" not in text and "--fixed-per-month" not in text


def test_what_was_not_measured_is_said_to_be_missing_not_guessed():
    text = report([_voice("a", 90, 0.045)], [], TODAY, calls_per_month=(300,), minutes=3)
    assert "Aún no hay ninguna llamada con el gasto del modelo apuntado" in text
    assert "Una llamada de 3,0 minutos (la duración pedida):" in text
    assert "| **Total** | **0,0900 $** | **0,0300 $** |" in text
    assert "| 300 | 900 | **27,00 $** |" in text
    for missing in ("**El modelo**", "**La línea telefónica**", "**Los gastos fijos**"):
        assert missing in text
    assert "Todavía no hay ninguna llamada" in report([], [], TODAY)
