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

- `AnthropicClient` is the adapter that exists today. Default model `claude-opus-5-5` at
  low effort; `VETDESK_LLM_MODEL` and `VETDESK_LLM_EFFORT` change that.
- `ScriptedClient` is a model that follows a script. The agent's tests run on it: no
  network, no cost, repeatable.

Adding a provider means writing one adapter and registering it in `create_client`.

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

Known gap for the voice layer: the waiting phrase ("Un momento, lo miro") is returned
together with the answer that follows the tool call. On the phone it has to be spoken
before the tool runs, which needs streaming (F5).
