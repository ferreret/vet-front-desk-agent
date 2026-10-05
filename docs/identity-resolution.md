# Identity resolution

Who is calling, how sure are we, and what should the agent ask next. This is the part of
the project that has to be right, and it involves no LLM: it is plain code, measured
against ground truth.

- Code: [`src/vetdesk/identity/`](../src/vetdesk/identity/) (the resolver) and
  [`src/vetdesk/legacy/`](../src/vetdesk/legacy/) (the adapter it reads the clinic through).
- Measurement: `uv run vetdesk identity eval`.
- Try one call: `uv run vetdesk identity resolve --number +34618065507 --name "Lucía Bibiloni Bosch" --pet Koko --town "Cala Tramuntana"`.

## What the resolver returns

| Decision | Meaning | What the agent may do |
|---|---|---|
| `resolved` | One client is confirmed | Read and write that client's data |
| `ask` | Not enough evidence yet. `ask_for` says what would help | Ask it. When `ask_for` is empty there is nothing left to ask: the caller stays unconfirmed |
| `not_found` | The name matches nobody on file | Treat the caller as not a client |

`ask_for` is one of `client_name`, `full_name` (both surnames), `confirm_name` (confirm or
spell it), `pet_name`, `confirm_pet`, `town`. Every candidate carries the reasons it is a
candidate, so the decision can be explained and logged.

## The policy

A caller is confirmed when their **name** matches a record and something more corroborates
it, leaving a single candidate. How much a name is worth depends on how much of it could be
compared:

| What could be compared | What confirms it |
|---|---|
| Full name: both surnames given, both on file | The phone on that record; or a pet name **and** the town on that record |
| The caller gave one surname, the record holds two | Nothing. Ask for both surnames |
| The record itself holds a single surname | Only the phone, and only if nobody else with that surname shares the number |

The calling number alone never confirms anybody. A name that matches nobody means "not a
client", whatever the phone says. A phone that points at somebody else does not make the caller
that person, and it does not let them be confirmed as anybody else either: a client on a
borrowed phone and an acquaintance who knows that client's name, pet and town bring exactly
the same evidence, so from another client's phone nobody is confirmed. They are served
unverified.

Being confirmed opens the caller's data and lets them book on their record. Cancelling or
moving an appointment asks for the phone as well: the call has to come from a number on
that record (see [agent.md](agent.md)). What a caller knows can be known by somebody else;
what cannot be undone asks for something they have.

## Names arrive through speech recognition

Each word of a name is graded against the record:

| Grade | Example | Counts as the name? |
|---|---|---|
| Exact, or the Catalan/Castilian form of the same given name | Margalida = Margarita | Yes |
| Sounds the same | Vázquez / Bázquez, Ginard / Jinard, Rocky / Roki | Yes |
| Similar: one sound away | Ferrer / Ferré, Puig / Puch, Lluna / Luna | Not until the caller confirms or spells it |
| Different | | No |

Once a name has been spelled, only its written form counts, with room for one typing
mistake in a surname on file (`Etseve` for Esteve). Given names get no such room: María and
Marta, Joan and Joana are different people who often share surnames and a landline.

Towns come from the short list of towns on file, so a badly heard one is still recognised
as long as it is clearly closer to one town than to any other.

## What measuring changed

The first version followed the obvious rule (name plus phone, or name plus pet) and passed
all the hand-shaped scenarios. A sweep of simulated calls then found what the scenarios had
not. Each finding became a rule, and a scenario or test that pins it down:

1. **One surname plus a pet is a coincidence waiting to happen.** Speech recognition drops
   a trailing surname; "Juan Martínez" with a dog called Chispa then matched a client who
   was not the caller. Now a pet only supports a full name.
2. **Relatives are namesakes and borrow phones.** Joan Lozano Ferrer called from the mobile
   stored on Joan Lozano Font's record; with the second surname lost, the phone picked the
   wrong Joan. Now a one-surname name confirms nothing when the record holds two.
3. **The owner link cannot be trusted to tell namesakes apart by spelling.** Two clients
   stored as `García López, Juan` and `Juan García López` looked distinguishable by string;
   they are not, because names get retyped while animals keep the old spelling. The adapter
   now links through a person key, and treats such animals as belonging to either.
4. **Two different people do share a full name and a pet name.** Of 220,000 simulated
   callers who were not clients, 8 had the same full name and the same pet name as a
   client. Asking for the town left 3. So a pet now confirms only together with the town.
5. **What a caller knows, a friend knows.** A caller said an appointment was a friend's;
   the model passed the friend's name, pet and town as the caller's own and the resolver
   confirmed the friend. The words were the caller's, so no check on the evidence catches
   it. The calling number did: it was on the caller's own record, not the friend's. Now a
   number on somebody else's record rules out confirming by pet and town. The price is the
   client who really is on a borrowed phone: about 870 of the 1,200 such calls in the sweep
   were identified before and are now served unverified.

## Results

Default clinic (seed 42, 400 clients), `uv run vetdesk identity eval`:

| | 82 scenarios (70 need identification) | Sweep: 10,800 calls |
|---|---|---|
| False identifications | **0** | **0** |
| Confirmed without enough evidence | 0 | 0 |
| Identified, of those who could be | 47 / 49 | 97.6% |
| Asked more than a perfect listener would | 2 | 0.5% |

The sweep is every client calling four ways (own phone, hidden number, giving one surname,
borrowed phone) at three levels of speech noise, plus 2,000 callers who are not clients.

Across eleven generated clinics of 200 to 1,500 clients: **174,567 calls, 0 false
identifications, 97.7% identified, 0.8% over-asked.**

## What it costs

Safety is paid for in questions. In the sweep, a client is asked 2.8 identity questions on
average, up from 2.2 before the town was required. The extra question only falls on callers
whose number is not on their record; a client calling from their own phone is confirmed as
soon as they give their name.

## What is not solved

- **Coincidences are rarer, not gone.** The town removed 5 of the 8 found in 220,000
  non-client calls. It is only as discriminating as the clinic's catchment area is spread:
  where nearly every client lives in the same town, the street on file is the factor to
  ask for instead. The evaluation reports coincidences separately from resolver mistakes,
  because no amount of careful listening avoids them; only more evidence does.
- **Typos in a given name on file** (`Deigo`) are never matched: the caller is treated as
  not a client. That is the safe direction, and it accounts for nearly all the missed
  identifications. `uv run vetdesk legacy inspect` lists these records as
  `given_name_suspect` so the clinic can fix them at the source.
- **The speech noise is simulated.** The rules that distort names were written for this
  project. Real recognisers will be measured in F5, and the phonetic comparison adjusted.
