"""Who hears and speaks on a LiveKit call: ElevenLabs by default, Google when told."""

import base64
import json

import pytest

pytest.importorskip("livekit.plugins.google")
from vetdesk.voice import livekit_app  # noqa: E402

KEY = json.dumps({"type": "service_account", "project_id": "a-project"})


@pytest.fixture
def by_google(monkeypatch):
    monkeypatch.setattr(livekit_app, "VOICE_SERVICE", "google")
    monkeypatch.setattr(livekit_app, "STT_LANGUAGES", ["es", "ca"])
    monkeypatch.setenv(livekit_app.GOOGLE_KEY, KEY)
    return monkeypatch


def test_by_default_elevenlabs_hears_and_speaks_as_before(monkeypatch):
    monkeypatch.setenv("ELEVEN_API_KEY", "a-key")
    stt, tts, speak_in = livekit_app._ears_and_mouth()
    assert type(stt).__module__.startswith("livekit.plugins.elevenlabs")
    assert type(tts).__module__.startswith("livekit.plugins.elevenlabs")
    assert speak_in("ca") is None  # one voice speaks every language


def test_google_listens_for_the_clinics_languages_and_speaks_in_the_callers(by_google):
    by_google.setattr(livekit_app, "GOOGLE_VOICES",
                      {"es": "es-ES-Chirp3-HD-Aoede", "ca": "ca-ES-Chirp3-HD-Leda"})
    stt, tts, speak_in = livekit_app._ears_and_mouth()
    assert type(stt).__module__.startswith("livekit.plugins.google")
    assert stt._config.languages == ["es-ES", "ca-ES"]
    assert tts._opts.voice.name == "es-ES-Chirp3-HD-Aoede" and tts.capabilities.streaming
    speak_in("ca")
    assert (tts._opts.voice.name, tts._opts.voice.language_code) == (
        "ca-ES-Chirp3-HD-Leda", "ca-ES")
    # A language with no voice of its own is said in the clinic's.
    speak_in("de")
    assert tts._opts.voice.name == "es-ES-Chirp3-HD-Aoede"


def test_a_voice_that_cannot_be_streamed_is_spoken_whole(by_google):
    by_google.setattr(livekit_app, "GOOGLE_VOICES",
                      {"es": "es-ES-Chirp3-HD-Aoede", "ca": "ca-ES-Standard-B"})
    _, tts, _ = livekit_app._ears_and_mouth()
    assert not tts.capabilities.streaming


def test_the_google_key_may_come_in_base64(by_google):
    by_google.setenv(livekit_app.GOOGLE_KEY, base64.b64encode(KEY.encode()).decode())
    by_google.setattr(livekit_app, "GOOGLE_VOICES", {"es": "es-ES-Chirp3-HD-Aoede"})
    stt, _, _ = livekit_app._ears_and_mouth()
    assert stt._credentials_info["project_id"] == "a-project"
