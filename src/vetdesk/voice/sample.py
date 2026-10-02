"""Hear a voice before choosing it: the same lines said by each ElevenLabs model.

    uv run python -m vetdesk.voice.sample --voice <voice id>

An accent comes mostly from the voice and partly from the model, and neither can be judged
from documentation. This writes one WAV file per model into data/voice-samples/, to be
compared by ear. It costs ElevenLabs credits: about one per character and model.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import time
from pathlib import Path

import aiohttp
from livekit import rtc
from livekit.plugins import elevenlabs

from .livekit_app import DATA, _load_env

MODELS = ("eleven_v4_turbo", "eleven_flash_v2_5", "eleven_multilingual_v2")
# What the agent really says: a greeting, a time, a phone number, a question.
LINES = {
    "es": "Clínica veterinaria Planeta Animal, buenos días. Tengo hueco el lunes 9 de "
          "noviembre a las cuatro y media de la tarde. Si es urgente, llame al 600 555 020. "
          "¿Me dice su nombre y sus dos apellidos, por favor?",
    "ca": "Clínica veterinària Planeta Animal, bon dia. Tinc hora dilluns 9 de novembre a "
          "les quatre i mitja de la tarda. Em pot dir el seu nom i els dos cognoms, si us "
          "plau?",
}


async def _say(model: str, voice: str | None, text: str, http: aiohttp.ClientSession):
    tts = elevenlabs.TTS(model=model, http_session=http,
                         **({"voice_id": voice} if voice else {}))
    started, first, frames = time.perf_counter(), None, []
    async for event in tts.synthesize(text):
        first = first if first is not None else time.perf_counter() - started
        frames.append(event.frame)
    return rtc.combine_audio_frames(frames), first


async def _run(voice: str | None, models: list[str], languages: list[str], out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    async with aiohttp.ClientSession() as http:
        for model in models:
            for language in languages:
                path = out / f"{model}-{language}.wav"
                try:
                    audio, first = await _say(model, voice, LINES[language], http)
                except Exception as error:  # a model this voice or key cannot use
                    print(f"{model} ({language}): failed: {error}")
                    continue
                path.write_bytes(audio.to_wav_bytes())
                print(f"{path}   first audio after {first:.2f} s, {audio.duration:.1f} s long")


def main() -> None:
    _load_env()
    parser = argparse.ArgumentParser(prog="vetdesk.voice.sample", description=__doc__)
    parser.add_argument("--voice", default=os.environ.get("VETDESK_TTS_VOICE"),
                        help="ElevenLabs voice id (default: VETDESK_TTS_VOICE, else theirs)")
    parser.add_argument("--models", default=",".join(MODELS), help="comma-separated model ids")
    parser.add_argument("--languages", default="es", help="es, ca or es,ca")
    parser.add_argument("--out", type=Path, default=DATA / "voice-samples")
    args = parser.parse_args()
    if not os.environ.get("ELEVEN_API_KEY"):
        raise SystemExit("Set ELEVEN_API_KEY in .env.")
    asyncio.run(_run(args.voice, args.models.split(","), args.languages.split(","), args.out))


if __name__ == "__main__":
    main()
