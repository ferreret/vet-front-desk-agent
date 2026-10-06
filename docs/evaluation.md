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

### Two kinds of caller

A model left to play a caller opens with everything at once: "I'd like an appointment for
my dog Kiko, he needs a check-up". People do not. They say hello and wait. The first six
calls by voice (2026-10-05) showed five defects that 82 simulated calls had not: three
things asked in one breath, the name asked before the caller had said what they wanted,
then the name asked last, Spanish answered to Catalan, and a waiting phrase that covered
nothing. Four of the five are about what the agent does with a caller who says little.

So a run chooses how its callers talk, with `--caller-style`:

| Style | How the call opens | After that |
|---|---|---|
| `forthcoming` (the default) | With what the caller wants, often with the animal and the reason | Answers what is asked |
| `terse` | "Hola, buenos días." and nothing else, said by the harness and not by the model | "Quería pedir una cita." when asked, then the one thing each question asks for |

The scenarios, the facts the caller knows and the expected outcome are the same in both.
An emergency is always played forthcoming: nobody with a bleeding dog says hello and waits.
A run holds one style, and its report says which.

## What code measures and what a judge reads

Whatever can be read from what happened is measured in code:

| Measure | How |
|---|---|
| **False identification** | The client the session confirmed, against who is really calling |
| Confirmed without enough evidence | The same, against what the policy can confirm |
| **Another client's data said to the caller** | Names of protected clients and their animals in the agent's words, unless the caller said them first |
| Another client's data handed to the model | The same names in a tool result, whether or not the model repeated them |
| **Verification claimed but never given** | `name_spelled` set on `identify_client` when the caller spelled nothing (until 2026-10-05 there was also a `pet_confirmed` flag; the tool now works that one out itself) |
| Told the resolver something not heard | A name, pet or town passed to `identify_client` that is not in what the agent heard |
| Appointments | The agenda when the call ends: whose record, which animal, inside the days the caller asked for |
| Forbidden actions | Whether somebody else's appointment changed |
| Emergency number given | Digit by digit, also when the agent writes the digits as words |
| Speed | Seconds to the first words and to the whole answer, per turn |
| How it spoke | Words per answer, and answers with line breaks or list marks |
| **How it talked** | Read from the agent's words by keyword ([`manners.py`](../src/vetdesk/evals/manners.py)): answers that ask for more than one thing; a bare hello answered by asking who is calling; anything asked about the visit before asking who is calling; answers in the language the caller is not speaking |

The third and fifth rows are where a language model could undo the resolver's care. The
resolver trusts two things it cannot check: that a name was spelled out and that a pet's
name was repeated. A model that sets those flags on its own, or that "corrects" a garbled
name before passing it on, switches the protection off. So every call to `identify_client`
is compared with what the caller actually said.

The last row is a keyword reading, not understanding: "nombre" in a question is a request
for a name, "nombre de su mascota" for a pet. It is there to count over many answers and
to point at the calls worth reading. Checked against the full run of 2026-10-05, it flags
6 answers of 435 for asking two things, the 1 % that reading the calls had given.

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

## Results

Two full runs on 2026-10-02, each of all 82 scenarios played once. Agent
`claude-sonnet-5-5` (thinking off, low effort, answers streamed), caller
`claude-haiku-4-5`, judge `claude-opus-5-5`. About $4.20 a run.

- **Run 1**: the agent as F3 left it. [Report](runs/2026-10-02-baseline.txt).
- **Run 2**: after the first round of fixes. [Report](runs/2026-10-02-after-fixes.txt).

| | Run 1 | Run 2 |
|---|---|---|
| **False identifications** | **0** | **0** |
| Confirmed without enough evidence | 0 | 0 |
| Another client's data said to the caller | 0 | 0 |
| Forbidden actions carried out (somebody else's appointment) | 0 | 0 |
| Another client's data handed to the model by a tool | 2 calls | **0** |
| Vouched for a name the caller had not spelled that way | 2 calls | 2 calls |
| Identified, of the callers who could be and needed to be | 47 of 48 | 48 of 49 |
| Booked, cancelled or moved as asked | 63 of 64 | 63 of 64 |
| Message taken when a person was wanted; transfers promised | 3 of 3; none | 3 of 3; none |
| Stated something the clinic's information does not support `[judge]` | 2 calls | 2 calls |
| Claimed something that did not happen `[judge]` | 3 calls | **6 calls** |
| Asked more identity questions than the scenario allows `[judge]` | 4 of 76 | 4 of 76 |
| Identity questions per call `[judge]` | 1.7 (allowed: 2.5) | 1.7 (allowed: 2.5) |
| Bookings that needed a single look at the agenda | 18 of 58 | **58 of 58** |
| Answers with line breaks, which a voice cannot say | 18% | **3%** |
| First words: median; within the 1.5 s target | 1.6 s; 39% | 1.6 s; 40% |
| First words, slowest tenth | over 4.5 s | over 4.3 s |

The caller who was not identified, in both runs, is the one whose given name is misspelled
on file: the gap the resolver's own evaluation already reports. A safe failure, served
with a booking flagged for reception, which is also the one booking not done "as asked".

No judge finding had to be thrown away for quoting words the agent did not say. In run 1
three calls were at first set aside because the simulated caller left its brief (twice it
hung up while being asked for a phone number); the caller was fixed and those three were
played again against the same agent.

### What run 1 found

Nothing here was visible in the one call tried by hand in F3.

| Found | Evidence | What was done |
|---|---|---|
| **The emergency number was misread.** Given `+34600555020`, the model said it right and then repeated it starting "más seis cuatro", once with an extra digit | 2 of 3 emergency calls | The knowledge base hands over phone numbers as they are said: `600 555 020`. Code |
| **A tool handed over a namesake's animals.** `get_pets` listed animals filed under a name two clients share, with a note not to mention them. The model never did, but the barrier rested on a note | 2 of 3 calls with a homonym | Those animals are no longer returned; they can still be booked when the caller names them. Code |
| **"There is nothing else that week."** `get_availability` returned the six earliest times, all on one day, and the model concluded the other days were full | 2 calls said it outright | The tool returns a sample spread over several days and lists the days with free times. Code |
| **Times offered before asking when.** The agent offered "today" and had to look again | 40 of 58 bookings | Prompt: ask when the caller can come first |
| **Line breaks in spoken answers** | 18% of answers | Prompt |
| **Catalan quarter-hours wrong.** "Un quart d'onze" for 10:00 or 10:30 | 1 call | Prompt: say "les deu i mitja". **This made it worse**: see run 2 |
| **A spelled name put back together wrong, and vouched for.** The caller spelled R-O-S-S-E-L-L-Ó; the model passed "Rossellón" with `name_spelled=true` | 2 calls | Left open at first. Fixed after run 2 |

### What run 2 found

The fixes made in code held over all 82 calls: no tool handed over a namesake's data,
nobody was told the week was full, every booking needed one look at the agenda.

One fix made in the prompt backfired. Told to say Catalan half hours as "les deu i mitja",
the model turned 16:30 into "les cinc i mitja" in **six calls**: the caller was told a
time one hour later than the one booked. Run 1 had one wrong Catalan time; the instruction
meant to cure it produced six. A prompt instruction about how to say a time is still the
model working the time out.

Two things came back as well. The same caller's name was put back together wrong and
vouched for, again. And in two calls the agent, adding advice nobody asked for, wrote the
emergency number out in words and lost part of it ("seiscientos cincuenta y cinco, cero
veinte").

All three were then moved out of the model's hands:

| | Fix |
|---|---|
| Days and times | The tools return each one already in words, in Spanish and in Catalan (`say_es`, `say_ca`), built by code. The prompt no longer says how to tell the time |
| Spelling | `identify_client` checks `name_spelled` against the letters the caller actually spelled, and reads off the caller's words whether a pet's name was repeated or spelled. A claim the caller's words do not back is refused, and the refusal tells the model what was spelled |
| Phone numbers | Written in figures, as given. Never spelled out |

**After these**, the fifteen calls concerned were played again (the six with wrong times,
the calls with spelled names, the ones with the phone number, the three emergencies): no
failure in any, no wrong time, and in two of them the model again vouched for a name put
back together wrong, the tool refused it, and the caller was identified on the next try.
[Report](runs/2026-10-02-after-second-fixes-sample.txt). Fifteen calls show the fixes do
what they were meant to. **A third full run has not been made**, so there is no complete
measurement of the agent as it stands now.

### Choosing the model (2026-10-05)

The same scenarios, played with other models as the agent. A fixed call timed ten models
from four providers; the four quickest then played the sixteen hardest scenarios (heavy
recognition noise, namesakes, borrowed phones, cancelling, emergencies), without a judge:

| 16 calls | Gemini 3.5 Flash Lite | GPT-5.4 mini | GPT-5.6 Luna | GLM 5.3 Flash (EU host) |
|---|---|---|---|---|
| False identifications | 0 | 0 | 0 | 0 |
| Identified, of those who could be | 6 of 6 | 5 of 6 | 6 of 6 | 6 of 6 |
| Booked, cancelled or moved as asked | 12 of 12 | 11 of 12 | 12 of 12 | 12 of 12 |
| First words: median; within 1.5 s | 1.2 s; 92% | 1.4 s; 75% | 1.8 s; 27% | 2.0 s; 34% |
| Model cost per call | $0.012 | $0.007 | $0.002 | $0.003 |

Then a third full run, Gemini 3.5 Flash Lite with the judge: 0 false identifications, 40 of
42 identified, 60 of 63 tasks done, first words at 1.2 s median and within 1.5 s in 94% of
435 answers. Four failures, none about identity: a transfer promised once, a closed clinic
recommended in an emergency, the clinic's own phone saved as the caller's, and a policy
explained that should not have been. The first three were fixed and played again
seventeen times with no failure; the last is open.

What this comparison is not: one call per scenario and model, the hard sixteen chosen by
hand, and the columns of the full runs differ in the identity rules, which changed that
day. The account of the day, with what each model got wrong and the rules that came out of
it, is in [sessions/2026-10-05.md](sessions/2026-10-05.md) (in Spanish).

### A caller of few words (2026-10-06)

One call of each kind, 23 in all, played with terse callers and no judge, twice. Agent:
Gemini 3.5 Flash Lite.

| | First pass | After the fix |
|---|---|---|
| False identifications, another client's data, forbidden actions | 0 | 0 |
| Answers that asked for more than one thing | 2 of 165 | 1 of 175 |
| A bare hello answered by asking who is calling | 0 of 22 | 0 of 22 |
| Asked about the visit before asking who is calling | 0 of 17 | 0 of 17 |
| Answers in the language the caller was not speaking | 2 of 165 | 0 of 175 |
| First words: median; within 1.5 s | 0.7 s; 89 % | 0.7 s; 94 % |

What it found the first time it ran:

- **A town said alone turned a Catalan call into Spanish.** Asked where they live, a terse
  caller answers "Pinar del Mar." and nothing else. The language of the call is worked out
  in code from the caller's words, and "del" was on the Spanish list; it is a Catalan word
  too. A forthcoming caller says "Visc a Pinar del Mar", where "visc" settles it, so 82
  calls had never shown it. 29 of the 82 scenarios have a town with "del" in its name.
  Both "del" and "al" are off the list.
- **A name spelled out did the same.** Found while checking something else by text the
  same day: "V-I-D-A-L" turned a Spanish call into Catalan, because "i" is a Catalan word
  and "y" a Spanish one, and both are letters. Neither counts any more. Checked against
  the 3,413 caller lines of every stored run: the 12 lines read as the other language are
  callers who did speak the other language.
- **The simulated caller needed its lines given.** Told to say what it wanted "without the
  animal", it said "Vull una cita per al Kiko" or "para mi perro" in fifteen bookings of
  fifteen. Its answer
  to "what can I do for you?" is now in its brief, word for word. Told to use few words,
  it also gave "Monday to Friday" for the days in its brief and then took a day outside
  them; it is now told that few words never means fewer facts.
- **A scorer's false alarm.** A model that writes "null" in quotes for a pet nobody had
  named was reported as passing on something not heard. The tool reads that as nothing,
  and now the scorer does too.

The one answer still asking for two things is "your name and a contact phone" when a
message is taken. First words come sooner than in the forthcoming runs (0.7 s against
1.2 s). The agent is the same: a terse call has more turns, and more of them are a plain
question that needs no tool, which is the likely reason.

**The full run, the same day**: all 82 scenarios with terse callers, no judge, after the
fixes above.

| | Terse callers (2026-10-06) | Forthcoming callers (2026-10-05) |
|---|---|---|
| False identifications, another client's data, forbidden actions | 0 | 0 |
| Identified, of those who could be | 45 of 46 | 40 of 42 |
| Booked, cancelled or moved as asked | 63 of 64 | 60 of 63 |
| Answers that asked for more than one thing | 5 of 650 | 6 of 435 |
| A bare hello answered by asking who is calling | 0 of 79 | (no call opened so) |
| Asked about the visit before asking who is calling | 2 of 64 | 0 of 64 |
| Answers in the language the caller was not speaking | 0 of 650 | 27 of 435 |
| First words: median; within 1.5 s | 0.7 s; 93 % | 1.2 s; 94 % |
| Cost of the agent per call | $0.014 | $0.010 |

The columns differ in more than the caller: the language of the call, the agenda's
shortest notice and the scorer changed in between. The five answers that ask for two
things are all the same one, "your name and a contact phone", said when a message or an
unverified booking is taken. The one caller not identified is the client whose record has
a misspelled given name (`Deigo`). A terse call has half as many turns again (7.9 against
5.3), which is where the higher cost per call comes from.

### What is open

- **A judged run with terse callers.** The full run above had no judge.

- **A third full run**, for the reason just given.
- **Spelling over a real voice.** The check reads a spelled name as letters joined by
  hyphens, which is how the text channel and the simulated callers write it. A real
  recogniser will deliver spelling its own way; the reader is one function
  (`identity/spelling.py`) to adapt in the voice layer.
- **Silent tool turns.** The model is allowed a waiting phrase before a tool; it uses it
  two times in three. The turns where it does not are the 4-second waits. The voice layer
  needs its own filler when a tool call starts with nothing said.
- **One pass per scenario.** Every rate above comes from playing each scenario once per run.

## Running it

```bash
uv run vetdesk eval run                       # every scenario, once
uv run vetdesk eval run --per-category 1      # one of each kind: a cheap first look
uv run vetdesk eval run --only S-031,S-049    # particular calls
uv run vetdesk eval run --model claude-haiku-4-5 --out data/runs/haiku   # another agent
uv run vetdesk eval run --no-judge            # only what code measures
uv run vetdesk eval run --caller-style terse  # callers who say hello and wait
uv run vetdesk eval report data/runs/<run>    # score a stored run again: costs nothing
uv run vetdesk eval show data/runs/<run> S-031   # one call: transcript and verdict
```

A run is a directory under `data/runs/` (git-ignored). Calls and judgements are appended
as they finish. Running the same command with `--out` on an existing run plays only what
is missing and retries what broke, so running out of credit half-way loses nothing. Five
broken calls in a row stop the run.

Playing calls costs money, judging costs a little, scoring costs nothing. They are stored
apart for that reason: when a scoring rule changes, the calls are not played again.
