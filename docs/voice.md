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

## First call with a microphone

2026-10-02. It answered, and it was unusable. Two faults, both in how it listened:

- **Spanish was turned into Dutch.** The caller said "Hola, quería pedir una cita para mi
  mascota"; the first partial transcripts were right, then the recogniser settled on Dutch
  and handed the agent "Hoi, ik wil een afspraak maken voor mijn huisdier". It had been
  given five visitors' languages as alternatives that same morning. On clean synthesized
  audio that had worked; on a real microphone it did not. The alternatives are back to
  Catalan only.
- **The caller's phrase was not closed for twenty seconds.** Closing it was left to the
  local voice detector, which did not see the pause. The recogniser now closes a phrase
  itself after 0.6 s of silence; tried on synthesized speech played in real time, the
  final transcript arrives 0.6 to 0.8 s after the voice stops.

Once it did answer, the chain measured 3.7 s from the caller stopping to the agent
starting: 0.8 s to close the phrase, 2.5 s for the model's first words (a first request,
with nothing cached), 0.2 s for the voice.

Not retried with a microphone since these two changes.

## First call through ElevenLabs Agents

2026-10-02, the same afternoon, from the test call in ElevenLabs' dashboard to the address
on this machine through a tunnel. **A whole booking, by voice, in two and a half minutes**:
a dog with fleas, the caller asked when he could come, offered three times on the
Saturday, name taken, not found on file, phone taken and read back, booked unverified and
flagged for reception. Every time was said as the tool wrote it.

- **Listening was not a problem.** Every line of the caller arrived whole and in Spanish.
- **Seconds from the request to the whole answer**: about 2 for a plain turn, 4 to 5 for
  one that uses a tool. ElevenLabs' own share (end of turn, voice) is not in that figure.
- **ElevenLabs asks again if it waits about four seconds.** One request was dropped and
  repeated with the same line; the agent answered it once, as designed.
- **The conversation id was not found** at first: ElevenLabs wraps the agent's prompt in
  text of its own, and the two marker lines were looked for at the start of a line. They
  are now found anywhere, and a later call arrived with its id.
- **Opening hours were stumbled over**: "a las cinco y media menos... perdone, a las
  16:30". The knowledge base now hands them over in words; on the next call the same
  question was answered "a las cuatro y media".

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

| | Spanish, Catalan | English | German, French, Italian, Russian |
|---|---|---|---|
| Set up at ElevenLabs, so the platform can be told to listen in it | Yes | Yes | Yes |
| Told apart in code from the caller's words | Yes | Yes | Yes (Russian by its alphabet) |
| The agent answers in it: set phrases, stock phrases, days and times, the emergency sentence | Yes | Yes | Yes |
| Measured with simulated callers, by text | Yes | Yes | 8 calls each, and 3 emergencies |
| Heard on a call with a voice | Yes | Yes | **No** |
| Read by somebody who speaks it | Yes | Yes | **No** |

A language is five things, and adding one is adding them: its telling words
(`language.py`), how a day and a time are said in it (`spoken.py`), the three set phrases
the agent says the same way every time and what it tells a caller with an emergency
(`agent/prompt.py`), and its stock phrases for silence and trouble. The instructions name
the languages and hold none of their phrases: the model is given the phrases of the
language the call is in, at the start and again when the language changes. With three
languages' phrases in the instructions, a caller speaking Catalan was asked "May I have
your full name, please?".

What telling seven languages apart took, each learnt from a call that went wrong:

- **One word changes the language only in a caller's first two lines.** After that it
  takes two. "Le unghie." has a Spanish word in it and "Vaccination annuelle." an English
  one; each carried its call off into another language.
- **A line that tells no language gets a reminder of the call's.** Given "Maria Ma Sala"
  and nothing else, on calls in French and in Russian, the model answered in Italian.
- **The emergency sentence is written in code, in each language, with the number in
  figures.** Left to the model to say in Italian, the emergency number came out in words,
  and wrong: "sessocento cinquanta cinquantaduecentoventi".

The tools hand over each day and time in one language, the call's (`say`). German, French,
Italian and Russian say the time of an appointment by the 24-hour clock.

Known limits:

- **Nobody who speaks German, French, Italian or Russian has read what the agent says in
  them.** The phrases and the times were written with an assistant and checked for
  consistency, not by a speaker.
- **A caller speaking Russian will rarely be identified.** The records are in Latin
  letters; a recogniser listening in Russian writes names in Cyrillic, and nothing here
  transliterates. Such a caller is served as anybody not on file is: an appointment under
  their word, flagged for reception. The simulated callers wrote their names in Latin
  letters, so the measurement does not show this.
- The clinic's information is written in Spanish and the model says it in the caller's
  language: opening hours told in German are the model's own wording.
- What the platform's recogniser writes when it hears German, French, Italian or Russian
  while set to Spanish is not known. With English it wrote English, and the rest followed.

Tried with synthesized phrases: English and German were transcribed and labelled
correctly. Catalan was transcribed correctly both times but labelled Spanish once and
Portuguese once, so the label is not what decides the language of the answer; the model
reads the words. Only the stock phrases follow the label, and fall back to Spanish.

### On the ElevenLabs route: the platform listens in one language at a time

The agent at ElevenLabs was set up in Spanish only, and on a call of 2026-10-06 its
recogniser, told Spanish, wrote a caller's Catalan as Spanish: "¿Puedo venir esta tarde?",
and "A dos cuartos de cinco", which is "dos quarts de cinc" (half past four) word for word
and means nothing in Spanish. The agent read Spanish and answered in Spanish. From our
side that cannot be told from a caller who has gone over to Spanish.

So the platform is told which language to listen in:

- The agent at ElevenLabs holds Catalan, English, German, Russian, French and Italian
  besides Spanish (`VETDESK_LANGUAGES`), and the platform's tool for changing language,
  `language_detection`.
  Adding them left the voice and the speech model as they were.
- That tool is meant for the platform's own model. No model of theirs is used here, and
  the language of a call is worked out in code from the caller's words, so our address
  calls it. The first line that tells another language is answered with the tool call and
  nothing else; the platform changes language and asks again, and the answer, worked out
  meanwhile, is ready. Tried by typing to the agent over its conversation socket: the
  platform reported the change to `ca` and the turn took about a second longer, once.
- Only for the languages the agent itself speaks, Spanish and Catalan today. English,
  German, Russian, French and Italian are set up at the platform and nothing more: telling them apart in
  code, an agent that answers in them, days and times said in them, and scenarios to
  measure it all are still to do.

Not yet heard on a call with a voice: whether the recogniser, once told Catalan, writes
Catalan.

### The recogniser hands some lines over twice

On the same day's calls the platform asked for an answer twice for the same line in four
turns of ten, 0.4 to 1.0 seconds apart, with the words written differently: "Hola, buen
día." and then "Hola, bon dia."; "934879642." and then the nine digits as words. It is not
the platform's "speculative turn", which is off. The platform drops the first answer and
keeps the second writing. A turn asked for again within three seconds is the same line:
if the first answer ran no tool, it is taken back (the model's conversation is rewound)
and the line answered as now written; if it ran one, the booking stands and the answer
already given is repeated.

### Trying the usual call by voice

The usual call is from a phone on the caller's own record: nine in ten such callers have
only to say their name. It cannot be tried from the platform's test panel, where the number
always arrives hidden, and with a real phone line there is a second obstacle: every record
in the clinic is invented, so nobody's real phone is on file. The server takes a setting,
`VETDESK_CALLER_STANDS_IN_FOR`, that says which real number calls as which of the clinic's
("real=clinic's"). The real number lives in the server's settings and nowhere in this
repository; the agent never sees it, and the records stay made up.

### The appointments: kept on disk, shown in a calendar

The agenda is a SQLite database of its own, apart from the clinic's records. In memory it
is a fresh book for every test and every simulated call. On the voice server it is a file
(`VETDESK_AGENDA`, on a mounted volume), written at once on every change, so a restart or
a new deployment loses nothing, and what was cancelled and what is past are kept too.

Nobody can look into a file on a server, so the appointments are also shown in a Google
Calendar ([`google_calendar.py`](../src/vetdesk/scheduling/google_calendar.py)), when
`VETDESK_GOOGLE_CALENDAR` and `VETDESK_GOOGLE_KEY` are set:

- An appointment booked, moved or cancelled on a call shows up in the calendar: the animal
  and the reason as the title, "(sin verificar)" when the caller could not be confirmed,
  and whose it is in the description.
- The agenda is the book and the calendar a picture of it. It is written to afterwards, on
  a worker thread: a slow or failing calendar makes no caller wait and fails no booking.
- One way only: what is changed by hand in the calendar changes nothing for the agent.
  Only events it made itself are ever read or changed.
- When the server starts the two are put in step: an appointment the calendar lacks is
  added to it.

For one morning the calendar was also where the appointments came back from after a
restart, the agenda being in memory. It worked, with Google as the only copy; the file is
the answer to "why is there no database for the appointments?".

Access is a Google service account that the calendar is shared with; nobody signs in. The
calendar's id and the key live in the server's settings and nowhere in this repository.

## Two ways to carry the voice

The first call with a microphone made one thing plain: the hard part of a voice line is
the listening (when has the caller finished, which language was that), and it is not the
part this project is about. So there are two ways in, and the agent is the same in both.

| | LiveKit Agents (`vetdesk.voice`) | ElevenLabs Agents (`vetdesk.voice.endpoint`) |
|---|---|---|
| Turn-taking, interruptions | LiveKit, tuned here | ElevenLabs' own |
| Hearing and speaking | ElevenLabs, called from here | ElevenLabs |
| Phone line or browser call | Needs a LiveKit server | Included |
| **Who answers** | **`FrontDeskAgent`**, through `llm_node` | **`FrontDeskAgent`**, as a "custom LLM" |
| Needs | Nothing for the console; a server for real calls | An address reachable from the internet |
| State | One call with a microphone, unusable; fixed since, not retried | One call: a whole booking by voice |

What is never handed over is the answering. An ElevenLabs agent with ElevenLabs' model and
a prompt in their dashboard is what the 2025 pilot was, and it did not know who it was
talking to. Here ElevenLabs gets an address to ask, and what answers is the agent with
the privacy barrier in code and the evaluation harness behind it.

### The address

```bash
uv run python -m vetdesk.voice.endpoint     # http://127.0.0.1:8013/v1/chat/completions
```

It speaks the OpenAI chat-completions format, streamed. The platform resends the whole
conversation with every request; the agent keeps its own, so each request is matched to
its call and only the caller's last line is taken from it. A line asked for twice is
answered once: a retry must not book twice.

Because the address has to be reachable from outside, every request must carry a key
(`VETDESK_ENDPOINT_KEY`, made on first run and kept in `.env`). Without it nothing is
answered.

The agent set up at ElevenLabs needs only two lines as its prompt, which tell the address
which call a request belongs to and who is calling:

```
vetdesk-conversation: {{system__conversation_id}}
vetdesk-caller: {{system__caller_id}}
```

### On a server

A tunnel to a laptop is for trying things. The image in the `Dockerfile` runs the same
address anywhere a container runs: it holds the synthetic clinic (built from its seed) and
no key. It needs two settings in its environment, `GEMINI_API_KEY` (the agent's default
model is Gemini's) and `VETDESK_ENDPOINT_KEY`, and refuses to start without the second.

```bash
docker build -t vetdesk-endpoint .
docker run --rm -p 8013:8013 -e GEMINI_API_KEY=... -e VETDESK_ENDPOINT_KEY=... vetdesk-endpoint
```

### What a call costs and how long it waits

From ElevenLabs' own records of the first three calls (13 turns), through the tunnel:

| From the caller falling silent to the agent's voice | 3.2 s (median) |
|---|---|
| of which: our address answering its first words | 1.9 s (1.5 to 5.3) |
| of which: the voice starting | 0.13 s |
| the rest: ElevenLabs deciding the caller has finished, and transcribing | about 1.1 s |

The model is most of the wait. Our own clock put the same first words at 1.8 to 2.3 s, so
the tunnel costs a few tenths of a second, which a server would save.

On 2026-10-05 the same address moved to a server and the agent to Gemini 3.5 Flash Lite.
One call, a whole booking in eight turns, again from ElevenLabs' records:

| | Tunnel, Claude Sonnet 5.5 (13 turns) | Server, Gemini 3.5 Flash Lite (8 turns) |
|---|---|---|
| From the caller falling silent to the agent's voice, median | 3.2 s | **1.9 s** |
| the same, slowest turn | 6.7 s | 2.4 s |
| of which: our address answering its first words | 1.9 s | 1.1 s |
| of which: the voice starting | 0.13 s | 0.12 s |

One call each side, so the difference is an indication and not a measurement; and two
things changed at once, the model and where the address runs.

ElevenLabs charged 150 credits a minute for these calls, at the reduced rate it applies to
test calls from its dashboard, and nothing for the model, since it is ours. The model's
cost was not recorded for these calls (it is logged per answer since); in the evaluation
harness a call of six turns costs about 2 US cents.

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

- **A filler for long waits, said by whoever can say it at once.** Three versions of a
  waiting phrase of our own were measured on calls and dropped. Said before every tool the
  model used in silence, a caller heard "un momento, por favor" six times in one call. Said
  only before the agenda, the turn that works out who is calling left 3.9 s of silence. Said
  by the clock, it still did not cover the wait: ElevenLabs holds back whatever it is sent
  until more text follows, so the phrase left our address at 2.0 s and was spoken at 2.8 s,
  with the answer. On that route the platform's own filler is used instead (its "soft
  timeout": after 2 s without an answer it says "Mmm...", no word of any language, since
  the agent is set up in Spanish only). The phrase by the clock, in the caller's language,
  remains for the LiveKit route, where nothing else fills a silence.
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
