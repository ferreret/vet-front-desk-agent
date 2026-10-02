# The agent

A language model, eight tools, and the loop between them. For now it talks in text
(`uv run vetdesk chat`); the voice layer (F5) will feed it what speech recognition hears
and read its answers aloud.

- Code: [`src/vetdesk/agent/`](../src/vetdesk/agent/), [`src/vetdesk/llm/`](../src/vetdesk/llm/),
  [`src/vetdesk/kb/`](../src/vetdesk/kb/), [`src/vetdesk/scheduling/`](../src/vetdesk/scheduling/).

## The model decides what to say, not what it may know

The privacy barrier is code, not prompt. Every tool that touches a client's data checks
that the identity resolver has confirmed the caller. Until then:

- `get_pets`, `list_appointments`, `cancel_appointment` and `reschedule_appointment` fail.
- `identify_client` answers with a status and one instruction ("ask for both surnames"),
  never with a name, a pet or a candidate from the records.
- Asking for somebody else's appointment gets the same answer as asking for one that does
  not exist, so the model cannot even confirm it is there.
- Even for a confirmed caller, `get_pets` leaves out animals filed under a name they share
  with another client: the file cannot say whose they are. The caller can still book for
  one by naming it.
- The model cannot vouch for what it did not hear. `identify_client` takes two flags that
  stop the resolver doubting a name: `name_spelled` and `pet_confirmed`. Each is checked
  against the caller's own words, and a claim they do not back is refused. In text, a
  name counts as spelled when written as letters joined by hyphens: `M-A-R-T-A P-O-N-S`.

The same rule holds for what the caller will act on. Days and times reach the model
already in words, in Spanish and Catalan, built by code; phone numbers come as they are
said. Each of these was first left to the model, and measured going wrong.

So there is nothing for the model to leak, however it is prompted or manipulated. A test
walks every identity scenario through the tools and checks that no name from the records
appears in any answer before confirmation.

## Tools

| Tool | Needs a confirmed caller |
|---|---|
| `identify_client` | No: it is how a caller gets confirmed |
| `get_availability` | No |
| `book_appointment` | No: an unconfirmed caller books under the name and phone they give, flagged for reception |
| `take_message` | No |
| `get_pets`, `list_appointments` | Yes |
| `cancel_appointment`, `reschedule_appointment` | Yes, and only the caller's own |

There is deliberately **no tool to transfer a call**. The 2025 pilot promised "I'll put
you through to reception" without being able to. This agent cannot promise what it cannot
do: it has `take_message`, and the tool's answer tells it to say reception will call back.

The clinic's information is not a tool. It is small enough to sit in the prompt, which
saves a round trip on every question; on a phone call that round trip is audible.

## One source for the clinic's facts

[`planeta_animal.toml`](../src/vetdesk/kb/planeta_animal.toml) holds the opening hours,
the emergency number, services, prices and frequent questions. The agent's prompt is
rendered from it and the agenda takes its opening hours from it; no fact is written by
hand anywhere else. Loading it fails if a placeholder or an empty value is left in, if a
phone number is malformed, or if the seasonal opening hours leave a day of the year
uncovered or covered twice.

## Any provider

The agent never imports a provider SDK. It talks to
[`LLMClient`](../src/vetdesk/llm/base.py): start a conversation, send what the caller said
or what the tools returned, get back text and tool calls. Each provider is one adapter,
and the conversation object keeps the provider's own message history, so reasoning blocks
and cache markers never leak into the agent.

- `AnthropicClient`: Claude models. Default `claude-sonnet-5-5`, low effort, thinking off,
  answers streamed; `VETDESK_LLM_MODEL`, `VETDESK_LLM_EFFORT` and `VETDESK_LLM_THINKING`
  change that.
- `GeminiClient`: Gemini models, with thinking kept to the minimum each model allows.
  Written from the SDK's documentation and tested against a stand-in; **not yet run against
  the live API**.
- `ScriptedClient` is a model that follows a script. The agent's tests run on it: no
  network, no cost, repeatable.

Adding a provider means writing one adapter and registering it in `create_client`. The
model id picks the provider: `--model gemini-flash-latest` needs nothing else.

## Trying it

```bash
cp .env.example .env        # then put your API key in it
uv run vetdesk generate
uv run vetdesk chat --number +34618065507 --now 2026-11-06T12:30 --verbose
```

`--number` is the calling number (leave it out for a hidden one), `--now` sets the date and
time of the call, and `--verbose` shows each tool call and its answer.

## What has and has not been verified

Verified, by tests that run without any model: the tools, the privacy barrier, the agenda,
the knowledge base validation, the agent loop (on the scripted model) and the shape of the
requests the Anthropic adapter builds.

Tried by hand, once: a real call with `claude-opus-5-5` on 2026-10-01. A client phoning
from another client's number asked for an appointment. The agent offered times, asked for
the name, asked for it to be spelled when it was mistyped, asked for the town, and booked
on the right record once the resolver confirmed the caller. Before that it said nothing
from the records.

**Measured since, on 2026-10-02:** every scenario played as a whole call by a simulated
caller, twice. No false identification, no client data given out and no transfer promised
in either run of 82 calls, and several things one good call could not show: a misread
emergency number, a tool that handed over a namesake's animals, another that made the
model say the week was full, appointment times said an hour wrong in Catalan. Those are
fixed, in code. Method, figures and what is still open are in
[evaluation.md](evaluation.md).

## How long callers wait

On the phone, the time the model takes is silence. `uv run vetdesk latency` plays one fixed
six-line call and reports how long the caller waits. Every figure below is a single run on
2026-10-01: samples, not statistics.

First, three models, each answer shown only when complete:

| Model | Median wait per answer | Slowest answer | Cost of the call |
|---|---|---|---|
| `claude-opus-5-5` | 4.5 s | 7.5 s | $0.072 |
| `claude-sonnet-5-5` | 3.0 s | 6.4 s | $0.036 |
| `claude-haiku-4-5` | 2.6 s | 40.3 s | $0.034 |

All three confirmed the caller and booked. Haiku drifted from the brief (it addressed the
caller as "tú", added line breaks, repeated the waiting phrase) and cost about as much as
Sonnet despite half the price per token, which suggests its prompt is not being cached;
not checked.

Then two changes, measured on Sonnet 5.5:

| Sonnet 5.5, answers streamed | First words: median | First words: slowest | Complete: median |
|---|---|---|---|
| Thinking on, low effort | 2.2 s | 4.9 s | 2.2 s |
| Thinking off (`between_tools`) | 1.8 s | 1.8 s, plus one 16.9 s outlier | 4.6 s |

- **Streaming alone changed little.** With thinking on, the model stays silent until it
  has finished thinking and running its tools, then says everything at once.
- **Without thinking, the model speaks first and works after.** It says a short line
  ("Un momento, lo miro"), then calls its tools. The caller hears something within about
  1.8 seconds on every turn, although the whole answer takes longer and is wordier.

So the defaults are now Sonnet 5.5, thinking off, answers streamed. The 1.5 s target is not
met yet, and two things are open:

- **Outliers.** One first request took 16.9 s and another, on Haiku, 40.3 s. Cause not
  established (possibly a retried request). They did not come back: of the 775 requests of
  the evaluation run on 2026-10-02, the slowest took 7.1 s. On a phone call a stalled
  request is a dead line all the same, so the voice layer needs a time limit per request
  and something to say when it is exceeded.
- **Quality without thinking** has now been measured over all the scenarios: see
  [evaluation.md](evaluation.md). That run also gives the latency figures to trust: 475
  answers instead of six. First words came after a median of 1.6 s, within the 1.5 s target
  39% of the time, and the long waits are the turns where the model reaches for a tool
  without saying anything first.

