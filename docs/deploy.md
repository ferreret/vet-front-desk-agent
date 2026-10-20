# Deploying the voice server

One image (the `Dockerfile` at the root), two kinds of deployment. What a deployment does
is decided by its settings alone; nothing in the code knows which one it is. No address,
key or real phone number is written in this repository.

| | Phone line | Browser demo, no ElevenLabs |
|---|---|---|
| Calls come in | By a phone number, through ElevenLabs Agents | From `/demo` in a browser, through LiveKit Cloud |
| Hears and speaks | ElevenLabs | LiveKit's own hosted models (or Google Cloud's) |
| The model | Gemini | Gemini, on its free tier |
| Accounts | ElevenLabs, Gemini, a SIP provider | LiveKit Cloud and a Gemini key; no card in either |

Both can run at once from the same commit, each with its own accounts: neither uses
anything of the other's. A setting left out keeps the behaviour it had before it existed.

## The browser demo, with LiveKit and no card

### Accounts

1. **LiveKit Cloud.** A project in Europe, with agent observability off. From its
   settings: the project's address (`wss://...`), an API key and its secret. On the free
   plan, hearing and speaking come out of the project's monthly inference credit (about
   2.50 $): when it is spent, calls stop being answered until the next month, and nothing
   is ever charged.
2. **Gemini**: an API key from Google AI Studio on a project with no billing, so on the
   free tier, with its rate limits. Everything the model is told is made up.
3. **A voice** of the provider in `VETDESK_TTS_MODEL` (Cartesia by default) that speaks
   Spanish from Spain, by its id, from that provider's voice library.

### Or Google Cloud for hearing and speaking

With a card on file and no monthly stop, but a larger free allowance. Instead of 3:

1. **Google Cloud**, one project for everything:
   - A Gemini API key (from Google AI Studio, on that project).
   - The *Cloud Speech-to-Text API* and the *Cloud Text-to-Speech API* enabled.
   - Billing enabled, which both speech APIs require even within their free allowance,
     and **a budget with an alert** on the project.
   - A service account with the *Cloud Speech Client* role, and a JSON key for it.
2. **The voices.** Google's voices are per language. Listen to them in the Text-to-Speech
   page of the Google Cloud console and pick one for Spanish (Spain) and one for Catalan.
   Chirp 3 HD voices are streamed sentence by sentence; a voice of another kind is
   spoken whole, and if one language has such a voice, they all are.

### Settings

| Setting | Value |
|---|---|
| `GEMINI_API_KEY` | The Gemini key |
| `VETDESK_ENDPOINT_KEY` | Any long random string; the server refuses to start without it |
| `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` | The LiveKit project's |
| `VETDESK_VOICE_SERVICE` | `livekit` (or `google`) |
| `VETDESK_TTS_VOICE` | With `livekit`: the voice's id |
| `VETDESK_TTS_MODEL` | Optional with `livekit`: `cartesia/sonic-3` by default |
| `VETDESK_STT_MODEL` | Optional: `google/gemini-3.5-transcribe-live` with `livekit` (told to listen for Spanish and Catalan), `chirp_3` with `google` |
| `VETDESK_GOOGLE_KEY` | With `google`: the service account's JSON key, in base64 (`base64 -w0 key.json`) |
| `VETDESK_GOOGLE_VOICES` | With `google`: `es=<Spanish voice>,ca=<Catalan voice>` |
| `VETDESK_GOOGLE_LOCATION` | Optional with `google`: `eu` by default |
| `VETDESK_DEMO_MINUTES_A_DAY`, `VETDESK_DEMO_MINUTES_A_CALL` | Optional. 60 and 3 by default: set the day's minutes to what the credit or the budget allows |
| `VETDESK_DEMO_AUTHOR`, `VETDESK_DEMO_AUTHOR_URL`, `VETDESK_DEMO_SOURCE` | Optional: the page says whose project it is, links to the code, and says in a line what the demo sets out to show |
| `VETDESK_DEMO_CONTACT` | Optional: a web address or a `mailto:`. The page then says it runs on free use that may be spent for the month, and links to it to ask for the demo |
| `VETDESK_CALLS`, `VETDESK_ADMIN_KEY` | Optional: write every call down in `/app/state/calls.db`, and the key to read them at `/calls/view` |
| `VETDESK_STT_LANGUAGES` | With `livekit`: `es`, so that the recogniser listens for Spanish only (see below) |
| `VETDESK_CLINIC` | `/app/state/clinic.toml`: the clinic's information on the volume, to change at `/clinic/edit` with the admin key |

Not set, on purpose: `ELEVEN_API_KEY` and `VETDESK_ELEVENLABS_DEMO_AGENT_ID` (with no
ElevenLabs account the page goes straight to LiveKit), the Telegram, calendar, transfer and
stand-in settings (a demo call tells reception nothing and has its own appointment book).

### On Dokploy

- An application from the GitHub repository, built with the `Dockerfile`, port 8013.
- A volume mounted at `/app/state` if calls are written down.
- It can have an address of its own, or a path of an address several demos share
  (`https://demos.example.dev/clinica-veterinaria/`), with the path taken off before the
  request reaches the server (Dokploy's *Strip path*, Traefik's `StripPrefix`). The page
  is served at the root as well as at `/demo`, asks for everything by relative address,
  and adds the final slash if it is missing, so it stays under its path.
- A domain with HTTPS in front of it. The page and its passes must be reachable from the
  internet; the sound is not, since the program that listens and speaks connects out to
  LiveKit Cloud. On a server at home that means a tunnel or a forwarded port to the
  reverse proxy, for HTTPS only.

### Checking it

The server's log says, at start:

- `the public demo is on: 4 callers to call as, ...`
- `the demo can also be carried by LiveKit: its voice program is started`

and then the voice program registering with LiveKit. Open `/demo`: there is no choice of
platform on the page, and a call goes by LiveKit. A voice program that cannot start says
why in the log (`Set VETDESK_TTS_VOICE ...`), and is started again.

### Spanish only

Heard on the first calls, on 2026-10-19 and 2026-10-20: Spanish works, at 2.9 to 4.4 s a
turn (1.7 to 2.2 s with ElevenLabs). Catalan went badly: with `livekit` it is said by the
Spanish voice. So the portfolio's demo speaks Spanish only: `VETDESK_STT_LANGUAGES=es`, and
`languages = ["es"]` in the clinic's information at `/clinic/edit`, so that the agent does
not change to another language. English would need a voice that changes with the language;
it was left out. Not measured yet: how many minutes a month the free credit lasts.

## The phone line, as it is

Deployed since 2026-10-05 and left unchanged. Its settings are the ones in `.env.example`
and [voice.md](voice.md): the ElevenLabs agent and key, the Gemini key, and optionally the
transfer number, Telegram, the calendar and the stand-in phones. Redeploying it from a
newer commit changes nothing as long as `VETDESK_VOICE_SERVICE` is not set.
