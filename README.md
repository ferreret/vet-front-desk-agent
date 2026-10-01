# vet-front-desk-agent

An AI phone front desk for veterinary clinics. It answers client calls by voice, works out
who is calling and which pet they have by querying the clinic's practice-management system,
answers with the clinic's own information, and books the appointment.

> **An agent that talks to real clients has to know when it does NOT know who is on the line.**

Veterinary practice-management systems are old. In the kind this project models, animals
are linked to their owner by the owner's **name**, not by a key. There are homonyms, phone
numbers that changed hands years ago, and clients with no animals at all. A naive agent
takes the first match, greets the wrong person, and tells them about someone else's dog.

So identification here is a resolution with a confidence level, not a `SELECT`; below the
threshold the agent asks instead of guessing; and the metric that matters most is the
**false identification rate**, which has to be zero.

## Status

| Phase | What | State |
|---|---|---|
| F1 | Synthetic generator: legacy-style database + call scenarios with ground truth | **Done** (2026-10-01) |
| F2 | Legacy adapter + identity resolution, with tests | **Done** (2026-10-01): 0 false identifications in 174,567 simulated calls |
| F3 | Agent with tools, knowledge base and mock agenda, over text | **Built** (2026-10-01), tested on a scripted model; not yet run against a real one |
| F4 | Evaluation harness with simulated callers | Next |
| F5 | Voice layer: real-time STT/TTS, latency budget, barge-in, Spanish and Catalan | |
| F6 | Public demo: a call from the browser | |
| F7 | Metrics and case study | |

The evaluation harness (F4) comes before the voice interface on purpose.

## Try it

Requires [uv](https://docs.astral.sh/uv/) and Python 3.12.

```bash
uv sync
uv run vetdesk generate --seed 42
uv run vetdesk scenarios list --category identity
uv run vetdesk scenarios explain S-031   # the call told as a story
uv run vetdesk scenarios show S-031      # the same call as JSON
uv run vetdesk legacy inspect            # what the adapter had to work around
uv run vetdesk identity resolve --number +34618065507 --name "Lucía Bibiloni Bosch" --pet Koko
uv run vetdesk identity eval             # the measurement
uv run pytest
```

To talk to the agent you need an API key for a language model:

```bash
cp .env.example .env                     # then put your key in it
uv run vetdesk chat --number +34618065507 --now 2026-11-06T12:30 --verbose
```

`generate` writes three files into `data/` (git-ignored, always rebuilt from the seed):

| File | What it is | Who reads it |
|---|---|---|
| `clinic.db` | The clinic as an old practice-management database would hold it | The agent side |
| `truth.json` | Who everybody really is, and which defect each record carries | Tests and the harness only |
| `scenarios.jsonl` | Phone calls with their expected outcome | Tests and the harness only |

## The synthetic clinic

The generator first builds a clean world (households, people, pets, who really answers
which phone), then degrades it into the legacy schema and labels every degradation:

- **Link by name**: `Animales.Cliente` holds the owner's name as text. No foreign key.
- **Homonyms**: clients with the same full name; parent and child stored with one surname.
- **Phones**: shared by a household, stale and now answered by a stranger, missing, typed
  in seven formats, two numbers in one field, notes mixed in.
- **Names**: upper case without accents, missing second surname, given name first, typos,
  Catalan names stored in their Castilian form.
- **`Nani`** (the client's animal count): zero for clients without animals, and sometimes
  out of step with the animals actually on file.
- **Animals**: owner name spelled differently from the client record, orphans whose owner
  no longer exists, species written five different ways, deceased animals.

## Call scenarios

Each scenario is one phone call with its ground truth: the calling number (or none), who
the caller really is, what speech recognition makes of the names they say, what a correct
identification looks like step by step, and which appointment should end up booked,
cancelled or moved. The format is documented in [docs/scenario-format.md](docs/scenario-format.md).

The reference answer comes from an oracle that hears every name perfectly and holds a
perfectly cleaned copy of the file, but is still limited to what is on file.

## Identity resolution

No LLM decides who is calling. A resolver, in plain code, reads the legacy database through
an adapter and returns `resolved`, `ask` (with what to ask next) or `not_found`, plus the
reasons behind every candidate.

**A caller is confirmed when their name matches a record and something more corroborates
it, leaving a single candidate**: the calling number is on that record, or a pet name and
the town both match it. A phone number alone never confirms anybody. A name that only
resembles a record, as speech recognition often delivers it, counts for nothing until the
caller has confirmed or spelled it.

Measured with `uv run vetdesk identity eval`, on the default clinic:

| | 82 scenarios (70 need identification) | Sweep: 10,800 calls |
|---|---|---|
| False identifications | **0** | **0** |
| Confirmed without enough evidence | 0 | 0 |
| Identified, of those who could be | 51 / 53 | 97.6% |
| Asked more than a perfect listener would | 2 | 0.6% |

Across eleven generated clinics: 174,567 calls, 0 false identifications, 97.7% identified.

Two things worth knowing before trusting those numbers. The first version of the policy
passed every scenario and still confirmed the wrong person in the sweep; four rules exist
only because measuring found them. And one risk is reduced, not closed: two different
people can share a full name and a pet name. Of 220,000 simulated non-client callers, 8
did; asking for the town left 3. Details, the cost in extra questions, and what is not
solved, in [docs/identity-resolution.md](docs/identity-resolution.md).

## The agent

A language model with eight tools, talking in text for now (`vetdesk chat`).

- **The privacy barrier is code, not prompt.** Tools that touch a client's data fail until
  the resolver has confirmed the caller, and until then no tool answer contains a name, a
  pet or an appointment from the records. The model decides what to say; it does not
  decide what it may know.
- **No tool transfers a call**, so the agent cannot promise to. It takes a message.
- **One validated source for clinic facts.** Opening hours, the emergency number, services
  and prices live in a single file that refuses to load with a placeholder, a malformed
  phone number or a gap in the opening hours. The prompt and the agenda are built from it.
- **Any provider.** The agent talks to a small interface; each provider is one adapter.
  Claude is the adapter that exists today.

This phase has been tested without a model: tools, barrier, agenda, knowledge base and the
agent loop on a scripted model. It has **not** yet been run against a real model; that is
what the evaluation harness (F4) is for. More in [docs/agent.md](docs/agent.md).

## Rules of the house

- **All data is synthetic.** Names are random combinations from word lists and phone numbers
  are random digits; any resemblance to a real person or number is accidental. No real
  clinic data is used, ever. The generator refuses to overwrite a database it did not create.
- The clinic in the demo, "Planeta Animal", is fictional.
- Written from scratch, with no code carried over from earlier projects.

## Built with AI assistance

This project is developed with the help of an AI coding assistant (Claude Code). Design
decisions, review and direction are the author's.

## License

MIT. See [LICENSE](LICENSE).
