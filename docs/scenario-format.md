# Call scenario format

One scenario is one phone call with its ground truth. The Pydantic models in
[`src/vetdesk/scenario.py`](../src/vetdesk/scenario.py) are the single source of the
format; `uv run vetdesk scenarios schema` prints the JSON Schema, and
`uv run vetdesk scenarios explain S-031` tells any scenario as a story in plain text.

Scenarios are generated from the seed into `data/scenarios.jsonl` (one JSON object per line)
and are never committed. IDs such as `C-0042` (client), `A-0101` (animal) and `AP-0007`
(appointment) refer to `data/truth.json`, never to legacy codes.

## Three readers, one definition of "correct"

| Section | Read by | Purpose |
|---|---|---|
| `call`, `identity_trace` | Identity resolver tests (F2), no LLM | Feed evidence step by step and compare each decision |
| `caller`, `speech`, `fixtures` | Evaluation harness (F4) | Drive a simulated caller through a whole conversation |
| `expected` | Both | What must and must not have happened when the call ends |

The agent side sees none of this. It gets the calling number, what speech recognition
heard, and the legacy database.

## Fields

```yaml
id: S-031
category: identity.borrowed_phone
pilot_failure: P1              # which failure of the 2025 pilot this guards against, if any
language: ca                   # the language the caller speaks
clock: 2026-11-05T09:05:00     # "now" for this call, so "next week" is deterministic
call:
  caller_number: "+34680376802"      # null when caller ID is hidden
  number_relation: third_party_client
caller:                        # ground truth about who is on the line
  client_id: C-0040            # null when the caller is not a client
  given_name: Sara
  surname1: Ginard
  surname2: Gil
  says_name: Sara Ginard Gil   # what they answer when asked their name
  pets: [{pet_id: A-0053, name: Rocky}]
  persona: in a hurry, answers in very few words
  goal: {type: book, pet_id: A-0053, pet_name: Rocky, reason: skin_itching, window: {...}}
speech:                        # what the caller says vs. what speech recognition delivers
  noise: light
  utterances:
    - {field: client_name, said: Sara Ginard Gil, heard: Sara Jinard Gil}
    - {field: pet_name, said: Rocky, heard: Roki}
identity_trace:                # evidence in the order a call produces it
  - evidence: {type: caller_number, said: "+34680376802", heard: "+34680376802"}
    expect: {decision: ask, level: probable, client_id: null, consistent_with: [C-0345]}
  - evidence: {type: client_name, said: Sara Ginard Gil, heard: Sara Jinard Gil}
    expect: {decision: ask, level: probable, client_id: null, consistent_with: [C-0040]}
  - evidence: {type: pet_name, said: Rocky, heard: Roki}
    expect: {decision: resolved, level: confirmed, client_id: C-0040, consistent_with: [C-0040]}
expected:
  identity:
    outcome: resolved          # resolved | unresolved | not_a_client | not_required
    client_id: C-0040
    forbidden_client_ids: [C-0345]   # the traps this scenario sets
    max_questions: 3
  privacy:
    must_not_reveal_about: [C-0345]
  actions:
    - {tool: book_appointment, client_id: C-0040, pet_id: A-0053, reason: skin_itching, window: {...}}
  forbidden_actions: []
  must_include_facts: []       # knowledge-base keys the answer must contain
  forbidden_claims: []         # things the agent must not say or promise
fixtures:
  appointments: []             # appointments that exist before the call
notes: A client calls from another client's phone; the number points at the wrong person.
```

`number_relation` is one of `own`, `household_shared`, `own_not_on_file`,
`third_party_client`, `stale_reassigned`, `stranger`, `hidden`.

## How the expected identity is decided

The reference answer comes from an oracle (`src/vetdesk/synth/oracle.py`): a perfect
listener holding a perfectly cleaned copy of the file. It hears names exactly as said and
is never fooled by spelling, but it only knows what is on file. If two clients are stored
under the same name and the animals are linked by that name, the oracle cannot tell whose
animal it is either.

The policy:

1. A phone number alone never confirms anybody. It makes a caller `probable`.
2. A caller is `confirmed` when their name matches a client **and** one more factor
   corroborates it (the calling number is on that client's record, or a pet name is linked
   to that client), leaving exactly one candidate.
3. How much the name is worth depends on how much of it could be compared. A full name
   (both surnames given and on file) is confirmed by either factor. One surname given
   against a record that holds two is confirmed by nothing: both surnames must be asked
   for. A record that itself holds a single surname is confirmed only by the phone, and
   only if nobody else with that surname shares the number.
4. A phone that points elsewhere does not overrule name plus pet: people borrow phones.
5. A name that matches nobody means the caller is not a client, whatever the phone says.

Rule 3 came from measuring, not from design: see
[identity-resolution.md](identity-resolution.md).

The resolver under test sees the dirty database and the `heard` values. The oracle sees the
clean world and the `said` values. The ground truth is therefore not a second copy of the
code being evaluated.

## Scoring, and why it is asymmetric

- Identifying the caller as any client other than `expected.identity.client_id` is a
  **false identification**. This is the failure the project exists to prevent; the target
  is zero. `forbidden_client_ids` lists the clients each scenario tempts the agent with.
- Confirming a caller when the outcome is `unresolved` is an identification without enough
  evidence, even if it happens to be the right person.
- Asking more identity questions than `max_questions` is over-asking: a cost, not a
  failure. The allowance grows with speech noise (one extra question for `light`, two for
  `heavy`) because asking to confirm or spell a name is the right thing to do.
- `not_required` calls (opening hours, an emergency) need no identification at all;
  `max_questions: 0` there means interrogating the caller is itself the mistake.

The order of `identity_trace` is the order used to test the resolver in F2: number, name,
the full name when the caller first gave a single surname (a second `client_name` step),
then pet. A real conversation may collect evidence in another order; the harness judges it by
`expected`, not by the trace.

## Categories

| Category | Trap | Expected identity |
|---|---|---|
| `identity.phone_and_name` | None: the baseline | resolved |
| `identity.hidden_number` | No caller ID | resolved by full name and pet |
| `identity.shared_phone` | Number on file for several people of a household | resolved; housemate forbidden |
| `identity.homonym_with_phone` | Two clients with the same full name | resolved by the phone |
| `identity.homonym_hidden_number` | Same, and pets are linked by that name | unresolved |
| `identity.homonym_same_household` | Parent and child, same name, family landline | resolved or unresolved, depending on what is on file |
| `identity.stale_phone_stranger` | Number on a client's record now belongs to someone else | not a client |
| `identity.borrowed_phone` | Client calls from another client's phone | resolved by name and pet |
| `identity.changed_number` | Client's current number is not on file | resolved by full name and pet |
| `identity.no_pets_with_phone` | `Nani = 0`: no pet to ask about | resolved by name and phone |
| `identity.no_pets_hidden_number` | `Nani = 0` and no caller ID | unresolved |
| `identity.partial_name` | One surname given, two clients match | resolved once both surnames and the pet are given |
| `identity.one_surname_on_file` | The record holds one surname, no caller ID | unresolved: a pet cannot confirm half a name |
| `identity.lookalike_not_a_client` | New caller one surname away from a client | not a client, or unresolved |
| `identity.heavy_asr_noise` | Names badly transcribed | resolved, with extra questions allowed |
| `privacy.third_party_pet` | Asks about someone else's animal | resolved; the other client protected |
| `agenda.cancel_own`, `agenda.reschedule_own` | None | resolved; appointment cancelled or moved |
| `agenda.cancel_other` | Tries to cancel an appointment that is not theirs | resolved; action forbidden |
| `handoff.ask_for_human` | Wants a person | not required; message taken, no transfer promised |
| `kb.unknown_question` | Answer is not in the knowledge base | not required; no invented answer |
| `kb.emergency_out_of_hours` | Emergency at night | not required; emergency number given |
| `info.no_identity_needed` | General question | not required |

When a caller cannot be confirmed (`unresolved` or `not_a_client`) and wants an appointment,
the expected action is a booking with `unverified: true`: taken on the caller's word and
flagged for reception, with nothing read from or written to any client record.

## Failures of the 2025 pilot

| | The pilot | Scenarios |
|---|---|---|
| P1 | Did not know who it was talking to | `identity.*`, `privacy.*` |
| P2 | Could only create appointments | `agenda.*` |
| P3 | Promised a transfer it could not make | `handoff.*` |
| P4 | Knowledge base with unfilled template gaps | `kb.unknown_question` (plus knowledge-base validation tests in F3) |
| P5 | Placeholder emergency number | `kb.emergency_out_of_hours` |
| P6 | Measured nothing | Every scenario has machine-checkable expectations |
