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

**Not measured yet.** One good call proves the plumbing, not the behaviour. How often the
agent asks one question too many, promises something it cannot do or answers beyond the
knowledge base is what the evaluation harness (F4) measures, over every scenario.

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
  established (possibly a retried request). On a phone call that is a dead line, so the
  voice layer needs a time limit per request and something to say when it is exceeded.
- **Quality without thinking is unmeasured.** One call went well. Whether the agent is as
  careful across all the scenarios is for the evaluation harness (F4); the privacy barrier
  does not depend on it, because it is code.

