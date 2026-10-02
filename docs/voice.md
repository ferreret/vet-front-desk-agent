# The voice layer

The same agent, heard and spoken. Speech recognition turns what the caller says into
text, the agent answers exactly as it does in text, and speech synthesis says the answer.
Who is calling and what may be said are decided where they always were: in the agent and
its tools.

- Code: [`src/vetdesk/voice/`](../src/vetdesk/voice/).

**State on 2026-10-02: the pieces work, the whole has not been heard.** The bridge to the
agent is covered by tests on a scripted model, and the application starts, sees the audio
devices and loads the clinic. ElevenLabs was tried without a microphone, by having it say
four phrases and transcribe them back (see below). Nobody has yet talked to it.

## First look at the real recogniser

Four synthesized phrases, said by ElevenLabs v4 Turbo and transcribed by Scribe v2
Realtime. A synthetic voice is clearer than a caller on a phone, so this is a best case.

| Said | Heard |
|---|---|
| Me llamo Miquel Rosselló López | Me llamo Miquel **Roselló** López |
| Em dic Xisca Bauzà Llull i visc a Santa Aina del Camp | Em dic Xisca **Bausà** Llull i visc a Santa Aina del Camp |
| eme, i, cu, u, e, ele. Erre, o, ese, ese, e, ele, ele, o con acento | M-I-Q-U-E-L-R-O-S-S-E-L-L-O |

- **Names are misheard even in the best case**, and in the way the simulated noise
  assumed: a doubled consonant lost, a "z" turned into "s".
- **A spelled name arrives as letters joined by hyphens**, but with the words run together
  and the accent gone. The spelling check was adapted to that: a name counts as spelled
  when its letters, in order, are among the letters the caller said.
- **The Catalan phrase was transcribed in Catalan but labelled Spanish.** See Languages.
- **Speed**: first audio from the voice after about 0.25 s; a transcript 0.6 to 1.0 s
  after the caller stops.

## Try it

```bash
uv sync --extra voice
# put ELEVEN_API_KEY in .env, next to the model's key
uv run python -m vetdesk.voice console
```

That talks through the computer's microphone and speakers. No server, no container, no
LiveKit account: LiveKit's console mode runs the whole pipeline in the terminal.
`console --list-devices` shows the audio devices, `--input-device` and `--output-device`
choose one, and `console --text` types instead of talking.

A microphone has no caller ID, so the call arrives with a hidden number. To try a call
"from" a number on file, set `VETDESK_CALLER_NUMBER`.

## Choosing the voice

The accent comes mostly from the voice and partly from the model, and neither can be
judged from documentation: ElevenLabs lists v4 Turbo's Spanish as Latin American and Flash
v2.5's as "Spain, Mexico". So the choice is made by ear:

```bash
uv run python -m vetdesk.voice.sample --voice <voice id>   # one WAV per model
```

The files land in `data/voice-samples/`. The voice and the model then go in `.env` as
`VETDESK_TTS_VOICE` and `VETDESK_TTS_MODEL`.

What the voice has to do: **Spanish from Spain, flawless**, and the languages of visitors
as well, because the clinic this is meant for is on a tourist coast. One voice speaks every
language of a multilingual model and keeps its own accent in all of them, so a voice from
Spain will speak English the way a receptionist from Spain does. Catalan matters less: no
synthesizer speaks it convincingly. `--languages es,en,fr,de,ca` adds samples in those.

## Languages

| | Spanish and Catalan | Visitors' languages |
|---|---|---|
| Hearing (Scribe v2 Realtime) | Yes | Yes: Spanish is the hinted language, with Catalan, English, French, German, Dutch and Italian as alternatives (`VETDESK_STT_LANGUAGES`) |
| Speaking (v4 Turbo) | Yes | Yes |
| The agent's own stock phrases (waiting, trouble) | Yes | Yes, in those six |
| The agent's prompt, the scenarios, days and times built in code | Yes | **No** |

So a visitor is heard and can be answered, but nothing about answering them has been
measured: the prompt still says "Spanish or Catalan", and a time in German would be the
model's own wording, which is exactly what went wrong in Catalan before code took it over.

Tried with synthesized phrases: English and German were transcribed and labelled
correctly. Catalan was transcribed correctly both times but labelled Spanish once and
Portuguese once, so the label is not what decides the language of the answer; the model
reads the words. Only the stock phrases follow the label, and fall back to Spanish.

## Who does what

| Piece | Does | Why this one |
|---|---|---|
| **LiveKit Agents** | The real-time part: listening, deciding when the caller has finished, letting them interrupt, playing the answer | Also carries the call from a browser (WebRTC) or a phone line (SIP), which the public demo needs |
| **ElevenLabs Scribe v2 Realtime** | Speech to text, Spanish with Catalan as a second language | Streams as the caller speaks |
| **`FrontDeskAgent`** | Every answer | The agent the evaluation harness measures. LiveKit reaches it through its `llm_node`; it brings no model of its own |
| **ElevenLabs v4 Turbo** | Text to speech | Catalan rules out Flash v2.5, the fastest model, which does not speak it |

LiveKit's own agent loop, tools and prompt are not used. Keeping one agent means the voice
line cannot behave differently from what was measured in text.

## What the voice line adds to the agent

- **A waiting phrase of its own.** Measured over 82 calls, the model reaches for a tool
  without a word in a third of the turns that use one, and those are the four-second
  silences. On a voice line the agent says "Un momento, por favor" (or its Catalan) when
  that happens. It is code, not a request to the model.
- **One turn at a time.** A caller can talk over the agent. The answer stops being heard at
  once, but the turn it belonged to runs on, so that a tool that was called still gets its
  result into the conversation; the next answer waits for it.
- **Something to say when the model cannot be reached.** Never silence.

## Not done yet

- **Hearing it.** See the state above.
- **Latency of the whole chain.** The harness measured the model: first words after a
  median of 1.6 s. Recognition, end-of-turn detection and synthesis add to that. The
  application logs each turn's timings as LiveKit reports them; they have not been
  collected.
- **Spelling by a real person.** A synthesized voice spelling a name in one breath is
  handled. A caller who spells slowly, with pauses or "be de Barcelona", is not known yet.
- **Answering visitors in their language, measured.** See Languages.
- **Real recognition errors.** The harness garbles names by rule. Playing synthesized
  callers through the real recogniser would replace that with measurement.
- **A call from a browser or a phone.** That needs a LiveKit server: their cloud, or the
  open-source server in a container. Console mode needs neither.
- **Interruptions cost a turn.** After being talked over, the agent finishes the turn it
  was on before starting the next; the model's history then holds an answer the caller did
  not hear in full.
