# The evaluation harness

The identity resolver was measured without any language model
([identity-resolution.md](identity-resolution.md)). This measures the whole agent: every
scenario is played as a phone call, from the greeting to the goodbye, against the same
agent that answers a real call.

- Code: [`src/vetdesk/evals/`](../src/vetdesk/evals/).
- Run it: `uv run vetdesk eval run`. Read a call: `uv run vetdesk eval show <run> S-031`.

## Three roles

| Role | Who plays it | What it knows |
|---|---|---|
| **Agent** | The real thing: prompt, tools, resolver, agenda, through `FrontDeskAgent` | The calling number, what speech recognition heard, the legacy database |
| **Caller** | A language model | What a real caller knows: their name, town, animals, phone and why they are calling |
| **Judge** | Another language model, with thinking on | The transcript, the tool results, the clinic's information and the caller's brief |

Only three things are staged: the clock (the scenario's), the appointments that exist
before the call, and the caller. Nobody but the scorer sees the ground truth or the
expected outcome.

Between caller and agent sits **simulated speech recognition**. The caller says
"Pablo Muñoz González"; the agent receives "Pavlo Muños", as the scenario recorded. Saying
the name again garbles it again. A name spelled letter by letter gets through intact,
which is the reason the agent asks for it.

## What code measures and what a judge reads

Whatever can be read from what happened is measured in code:

| Measure | How |
|---|---|
| **False identification** | The client the session confirmed, against who is really calling |
| Confirmed without enough evidence | The same, against what the policy can confirm |
| **Another client's data said to the caller** | Names of protected clients and their animals in the agent's words, unless the caller said them first |
| Another client's data handed to the model | The same names in a tool result, whether or not the model repeated them |
| **Verification claimed but never given** | `name_spelled` or `pet_confirmed` set on `identify_client` when the caller neither spelled nor repeated anything |
| Told the resolver something not heard | A name, pet or town passed to `identify_client` that is not in what the agent heard |
| Appointments | The agenda when the call ends: whose record, which animal, inside the days the caller asked for |
| Forbidden actions | Whether somebody else's appointment changed |
| Emergency number given | Digit by digit, also when the agent writes the digits as words |
| Speed | Seconds to the first words and to the whole answer, per turn |
| How it spoke | Words per answer, and answers with line breaks or list marks |

The third and fifth rows are where a language model could undo the resolver's care. The
resolver trusts two things it cannot check: that a name was spelled out and that a pet's
name was repeated. A model that sets those flags on its own, or that "corrects" a garbled
name before passing it on, switches the protection off. So every call to `identify_client`
is compared with what the caller actually said.

A judge reads only what needs a reader:

- every question asked in order to identify the caller, to count against the scenario's
  allowance;
- a promise to transfer the call;
- a statement about the clinic that its information does not support, or veterinary advice;
- a claim of having done something the tool results do not show;
- an answer in the wrong language;
- whether a question the clinic's information can answer was answered.

## Why the numbers can be trusted, and where they cannot

**The judge is kept on a short lead.** It fills in a fixed checklist through a strict
schema. It never sees the expected outcome, so it cannot grade towards it. Every finding
must quote the agent, and a quote that is not in the transcript is thrown away and
counted. Its findings are reported apart, marked `[judge]`.

**The caller is checked too.** A simulated caller that invents a surname would get the
agent blamed for not finding the client. The judge reports any call where the caller left
its brief; those calls are set aside as `invalid` instead of being scored.

**Plumbing is not scored as behaviour.** A call broken by a model error, one that never
ended, and one with a caller off its brief are each counted apart. What must be zero is
still counted over every call that took place: a false identification in a call that later
broke is still a false identification.

**Verdicts are asymmetric,** like the resolver's. Confirming the wrong person, giving out
data, or touching somebody else's appointment are failures. Not managing to confirm a
caller, who then gets a booking flagged for reception, is a shortfall: safe, but the
caller was served worse than they could have been.

**The harness was tested before it was trusted.** On scripted models, with no network: an
agent that does everything right passes, one that does nothing fails safely, one that
claims a spelling it never got is caught, and a hand-written call exists for every way of
failing.

What it does not give:

- **Statistical certainty.** 82 scenarios played once. A rate measured on 82 calls is good
  to about ±10 points, and zero failures in 70 calls only bounds the true rate below about
  4%. The guarantee of zero false identifications does not rest on this run: it rests on
  the resolver being code, measured over 174,567 calls. This run checks that the model in
  front of it does not find a way around.
- **Real callers.** A model plays them, following a brief.
- **Real speech recognition.** The noise is the scenario's, applied to names only. Real
  recognisers arrive with the voice layer (F5).
- **An independent judge.** Judge and agent are from the same model family.
- **Latency of the voice.** Seconds here are the model's; speech recognition and synthesis
  will add theirs.

## Running it

```bash
uv run vetdesk eval run                       # every scenario, once
uv run vetdesk eval run --per-category 1      # one of each kind: a cheap first look
uv run vetdesk eval run --only S-031,S-049    # particular calls
uv run vetdesk eval run --model claude-haiku-4-5 --out data/runs/haiku   # another agent
uv run vetdesk eval run --no-judge            # only what code measures
uv run vetdesk eval report data/runs/<run>    # score a stored run again: costs nothing
uv run vetdesk eval show data/runs/<run> S-031   # one call: transcript and verdict
```

A run is a directory under `data/runs/` (git-ignored). Calls and judgements are appended
as they finish. Running the same command with `--out` on an existing run plays only what
is missing and retries what broke, so running out of credit half-way loses nothing. Five
broken calls in a row stop the run.

Playing calls costs money, judging costs a little, scoring costs nothing. They are stored
apart for that reason: when a scoring rule changes, the calls are not played again.
