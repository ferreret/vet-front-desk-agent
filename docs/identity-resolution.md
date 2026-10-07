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
| Similar: one sound away | Ferrer / Ferré, Puig / Puch, Lluna / Luna | Not until the caller confirms or spells it, with the one exception below |
| Different | | No |

**The exception.** Spelling a name is the most tedious thing a caller is asked for, so it is
skipped when there is nothing left to doubt: one surname is a sound away, the given name
was heard right, nobody else on file resembles the name, and either the call comes from
that client's phone or the pet and the town agree. The given name never gets this room:
brothers and sisters share both surnames, the landline, the pet and the town, and Joan and
Joana are one sound apart.

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

6. **Most spelling was asked of callers there was nothing left to doubt about.** In the
   sweep 29% of clients were asked to spell their name (41% of those misheard). Letting one
   slightly-off surname count when the phone, or the pet and town, back it brought that to
   22% with no false identification, over four clinics and 81,000 calls. The same room
   for the given name confirmed 6 in 10 non-client brothers and sisters as the client, and
   "the word heard is not a real name" still confirmed 7 in 100: both were thrown away.

## Results

Default clinic (seed 42, 400 clients), `uv run vetdesk identity eval`:

| | 82 scenarios (70 need identification) | Sweep: 10,800 calls |
|---|---|---|
| False identifications | **0** | **0** |
| Confirmed without enough evidence | 0 | 0 |
| Identified, of those who could be | 47 / 49 | 97.6% |
| Asked more than a perfect listener would | 2 | 0.3% |

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
- **A near-namesake with the same pet and town.** Somebody whose first surname is one
  letter from a client's, with the same given name, second surname, pet name and town, is
  confirmed as that client: in two of three such calls before the exception above, because
  a spelled surname is allowed one typing mistake on file, and in four of five with it.
  None turned up among 36,000 random non-clients; the case had to be built on purpose.
- **Typos in a given name on file** are not matched, with one exception: the caller is
  treated as not a client. That is the safe direction. `uv run vetdesk legacy inspect`
  lists these records as `given_name_suspect` so the clinic can fix them at the source.

  The exception, since 2026-10-07: **two neighbouring letters swapped** (`Deigo` for
  Diego). It counts when what is on file is a name nobody else in the clinic has and what
  the caller says is one that others do, both surnames are heard right, one client fits,
  and the rest backs it as for any name: the call comes from that client's phone, or the
  pet and the town agree. Heard or spelled, it is the same caller. Reception is told that
  the record looks mistyped. It came from the first call made from the public demo: a
  caller on his own phone, asked twice to spell "Diego". Measured again that day on the
  rule as built: no false identification in the sweep (10,800 calls), nine more calls
  identified in it, and of 236 relatives built on purpose (the client's surnames, a given
  name one letter from the record, from the client's phone or knowing pet and town) not
  one confirmed that was not confirmed before. The same measure for any single letter
  instead of two swapped: 124 more relatives confirmed from the client's phone. And for
  the calling number alone confirming whoever's record it is on: 2,574 false
  identifications in the 10,800 calls, 1,014 of them clients on another client's phone
  and 1,557 people who are not clients and hold a number that used to be one's.
  The sweep puts every client on another client's phone once, so those figures say
  whether it ever goes wrong, not how often it would. Measured too, as a middle way, what
  a receptionist does: the number on a single record and the given name alone. 48 false
  identifications in 5,424 calls with a number: people who share a given name with whoever
  the number belongs to. Not done either: the count this project stands on is none.

  Forgiving them was measured on 2026-10-06, for a caller who spells their name and calls
  from a phone on the record. Five of the 400 clients have a misspelled given name on file
  (`Deigo`, `Mria`, `lberto`, `oana`, `armen`). Against each rule, the sweep and 292 calls
  built on purpose: a relative who is not a client, with the same two surnames and a given
  name one letter from the one on file, calling from the client's phone.

  | Rule for a spelled given name one letter from the record | Clients recovered, of 5 | Relatives confirmed as the client, of 292 |
  |---|---|---|
  | None (today) | 0 | 1 |
  | Any single letter | 4 | 281 |
  | Only when nobody else on file has the record's name and the name spelled is a common one | 3 | 3 |
  | The same, and only two neighbouring letters swapped | 1 | 1 |

  The one confirmed today is `Helena` calling as the relative of an `Elena`: the two sound
  the same, and a name heard cannot tell them apart. The third rule adds an `Ana` confirmed
  as the `oana` that was a Joana, and a `Bel` as a `Biel` who is the only one in the clinic:
  a rare name looks like a typo. The last rule adds nobody and recovers one client in 400.
  None of the rules produced a false identification in the sweep itself, which has no
  relatives in it: the 292 calls are what showed the difference. The rule stays as it is.
- **The speech noise is simulated.** The rules that distort names were written for this
  project. Real recognisers will be measured in F5, and the phonetic comparison adjusted.
