"""Command line: `vetdesk generate` builds the synthetic clinic and its call scenarios."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

import anthropic

from .agent import FrontDeskAgent
from .dbguard import ForeignDatabaseError
from .evals.identity import Probe, format_report, probes_from_scenarios, run_probe, summarize
from .evals.latency import PRICES, format_timings, time_call
from .evals.sweep import sweep_probes
from .explain import Labels, explain
from .identity import Evidence, IdentityResolver
from .kb import load_kb
from .legacy import LegacySqliteSource
from .llm import PROVIDERS, LLMError, Usage, create_client, provider_of
from .scenario import Scenario, dump_jsonl, load_jsonl
from .scheduling import SqliteAgenda
from .synth import GeneratorConfig, generate_world
from .synth.legacy_db import defect_counts, export_truth, write_legacy_db
from .synth.scenarios import ScenarioError, generate_scenarios

DB_NAME = "clinic.db"
TRUTH_NAME = "truth.json"
SCENARIOS_NAME = "scenarios.jsonl"


def _generate(args: argparse.Namespace) -> int:
    try:
        world = generate_world(GeneratorConfig(seed=args.seed, n_clients=args.clients))
        scenarios = generate_scenarios(world)
        out: Path = args.out
        out.mkdir(parents=True, exist_ok=True)
        write_legacy_db(world, out / DB_NAME)
    except (ValueError, ScenarioError, ForeignDatabaseError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    truth = json.dumps(export_truth(world), ensure_ascii=False, indent=1)
    (out / TRUTH_NAME).write_text(truth + "\n", encoding="utf-8")
    (out / SCENARIOS_NAME).write_text(dump_jsonl(scenarios), encoding="utf-8")

    print(f"seed {args.seed}: {len(world.clients)} clients, {len(world.pets)} animals")
    print(f"  {out / DB_NAME}\n  {out / TRUTH_NAME}\n  {out / SCENARIOS_NAME}")
    print("defects planted:")
    for label, count in defect_counts(world).items():
        print(f"  {count:4d}  {label}")
    print(f"scenarios: {len(scenarios)}")
    for category, count in Counter(s.category for s in scenarios).items():
        print(f"  {count:4d}  {category}")
    return 0


def _load(path: Path) -> list[Scenario] | None:
    if not path.exists():
        print(f"error: {path} not found; run `vetdesk generate` first", file=sys.stderr)
        return None
    return load_jsonl(path.read_text(encoding="utf-8"))


def _list(args: argparse.Namespace) -> int:
    scenarios = _load(args.file)
    if scenarios is None:
        return 1
    for s in scenarios:
        if s.category.startswith(args.category or ""):
            identity = s.expected.identity
            target = identity.client_id or "-"
            number = s.call.caller_number or "hidden"
            print(f"{s.id}  {s.category:34} {s.language}  {s.speech.noise:5}  {number:13} "
                  f"{identity.outcome:12} {target}")
    return 0


def _show(args: argparse.Namespace) -> int:
    scenarios = _load(args.file)
    if scenarios is None:
        return 1
    for s in scenarios:
        if s.id == args.id:
            print(json.dumps(s.model_dump(mode="json"), ensure_ascii=False, indent=2))
            return 0
    print(f"error: no scenario {args.id}", file=sys.stderr)
    return 1


def _explain(args: argparse.Namespace) -> int:
    scenarios = _load(args.file)
    if scenarios is None:
        return 1
    # With truth.json next to the scenarios, ids are shown with the person's name.
    truth_path = args.file.parent / TRUTH_NAME
    truth = json.loads(truth_path.read_text(encoding="utf-8")) if truth_path.exists() else None
    for s in scenarios:
        if s.id == args.id:
            print(explain(s, Labels(truth)))
            return 0
    print(f"error: no scenario {args.id}", file=sys.stderr)
    return 1


def _schema(args: argparse.Namespace) -> int:
    print(json.dumps(Scenario.model_json_schema(), ensure_ascii=False, indent=2))
    return 0


def _clinic(db: Path):
    try:
        return LegacySqliteSource(db).load()
    except FileNotFoundError:
        print(f"error: {db} not found; run `vetdesk generate` first", file=sys.stderr)
    except ForeignDatabaseError as error:
        print(f"error: {error}", file=sys.stderr)
    return None


def _legacy_inspect(args: argparse.Namespace) -> int:
    clinic = _clinic(args.db)
    if clinic is None:
        return 1
    print(f"{args.db}: {len(clinic.clients)} clients, {len(clinic.animals)} animals")
    print("what the adapter found while cleaning up:")
    kinds = Counter(issue.kind for issue in clinic.issues)
    for kind, count in sorted(kinds.items()):
        example = next(i for i in clinic.issues if i.kind == kind)
        print(f"  {count:4d}  {kind:24} e.g. {example.table} {example.code}: {example.detail}")
    return 0


def _identity_resolve(args: argparse.Namespace) -> int:
    clinic = _clinic(args.db)
    if clinic is None:
        return 1
    evidence = Evidence(
        caller_number=args.number,
        client_name=args.name,
        name_verified=args.name_verified,
        pet_name=args.pet,
        pet_verified=args.pet_verified,
        town=args.town,
    )
    resolution = IdentityResolver(clinic).resolve(evidence)
    print(f"decision: {resolution.decision}   level: {resolution.level}")
    print(f"why:      {resolution.why}")
    if resolution.ask_for:
        print(f"ask for:  {resolution.ask_for}")
    for candidate in resolution.candidates[:10]:
        reasons = "; ".join(candidate.reasons(args.name_verified)) or "calling number only"
        print(f"  client {candidate.client.code}: {reasons}")
    return 0


def _identity_eval(args: argparse.Namespace) -> int:
    scenarios = _load(args.data / SCENARIOS_NAME)
    clinic = _clinic(args.data / DB_NAME)
    if scenarios is None or clinic is None:
        return 1
    truth = json.loads((args.data / TRUTH_NAME).read_text(encoding="utf-8"))
    client_ids = {c["legacy_codigo"]: c["client_id"] for c in truth["clients"]}
    resolver = IdentityResolver(clinic)

    def run(title: str, probes: list[Probe]) -> int:
        summary = summarize([run_probe(resolver, probe, client_ids) for probe in probes])
        print(format_report(title, summary))
        return summary.false_identifications + summary.verdicts["unsupported_identification"]

    failures = run(f"SCENARIOS ({args.data / SCENARIOS_NAME})", probes_from_scenarios(scenarios))
    if args.strangers >= 0:
        world = generate_world(GeneratorConfig(**truth["config"]))
        if export_truth(world) != truth:
            print("error: data is out of date; run `vetdesk generate` again", file=sys.stderr)
            return 1
        probes = sweep_probes(world, strangers=args.strangers)
        print()
        failures += run("SWEEP (every client, four ways of calling, three noise levels, "
                        f"plus {args.strangers} callers who are not clients)", probes)
    return 1 if failures else 0


def _load_env(path: Path = Path(".env")) -> None:
    """Read KEY=VALUE lines from .env into the environment, without overriding it."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.strip().partition("=")
        if separator and key and not key.startswith("#"):
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def _explain_llm_error(error: Exception, provider: str | None, model: str | None) -> None:
    """Say what to do about the usual credential problems."""
    if (provider or provider_of(model) or "anthropic") != "anthropic":
        return
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("Set ANTHROPIC_API_KEY in .env to talk to the agent.", file=sys.stderr)
    elif "workspace" in str(error) and not os.environ.get("ANTHROPIC_WORKSPACE_ID"):
        print("This key is not tied to a workspace: set ANTHROPIC_WORKSPACE_ID in .env, "
              "or use a key created inside a workspace.", file=sys.stderr)


def _chat(args: argparse.Namespace) -> int:
    clinic = _clinic(args.data / DB_NAME)
    if clinic is None:
        return 1
    _load_env()
    kb = load_kb()
    started = datetime.now()
    opened = args.now or started

    def now() -> datetime:  # the call's clock: starts at --now and then runs normally
        return opened + (datetime.now() - started)

    try:
        llm = create_client(args.provider, args.model)
        agent = FrontDeskAgent(llm, clinic, kb, SqliteAgenda(kb, now), now)
        call = agent.start_call(args.number)
        print(f"(calling from {args.number or 'a hidden number'}; an empty line hangs up)\n")
        print(f"agent > {call.greeting}")
        total = Usage()
        while True:
            try:
                said = input("you   > ").strip()
            except EOFError:
                break
            if not said:
                break
            turn = call.say(said)
            total += turn.usage
            if args.verbose:
                for event in turn.events:
                    arguments = json.dumps(event.arguments, ensure_ascii=False)
                    result = json.dumps(event.result, ensure_ascii=False)
                    print(f"        [{event.name} {arguments} -> {result}]")
            print(f"agent > {turn.text}")
            if args.verbose:
                steps = " + ".join(f"{seconds:.1f}" for seconds in turn.latencies)
                print(f"        ({turn.seconds:.1f} s waiting for the model: {steps})")
    except (LLMError, anthropic.AnthropicError) as error:
        print(f"error: {error}", file=sys.stderr)
        _explain_llm_error(error, args.provider, args.model)
        return 1
    session = call.session
    confirmed = f"client {session.client.code}" if session.client else "not confirmed"
    print(f"\ncaller: {confirmed}")
    for message in session.messages:
        print(f"message for reception: {message.text} ({message.contact_name}, "
              f"{message.contact_phone})")
    print(f"tokens: {total.input_tokens} in, {total.output_tokens} out, "
          f"{total.cache_read_tokens} read from cache")
    return 0


def _latency(args: argparse.Namespace) -> int:
    scenarios = _load(args.data / SCENARIOS_NAME)
    clinic = _clinic(args.data / DB_NAME)
    if scenarios is None or clinic is None:
        return 1
    _load_env()
    kb = load_kb()
    scenario = next(s for s in scenarios
                    if s.category == "identity.borrowed_phone" and s.speech.noise == "none")
    timings = []
    for model in args.models.split(","):
        print(f"{model}:")
        try:
            llm = create_client(args.provider, model.strip())
            timings.append(time_call(model.strip(), llm, clinic, kb, scenario, print))
        except (LLMError, anthropic.AnthropicError) as error:
            print(f"  error: {error}", file=sys.stderr)
    if timings:
        print()
        print(format_timings(timings))
    return 0 if timings else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vetdesk", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    generate = commands.add_parser("generate", help="build the synthetic clinic and scenarios")
    generate.add_argument("--seed", type=int, default=42)
    generate.add_argument("--clients", type=int, default=400)
    generate.add_argument("--out", type=Path, default=Path("data"))
    generate.set_defaults(run=_generate)

    scenarios = commands.add_parser("scenarios", help="inspect generated scenarios")
    actions = scenarios.add_subparsers(dest="action", required=True)
    default_file = Path("data") / SCENARIOS_NAME
    listing = actions.add_parser("list", help="one line per scenario")
    listing.add_argument("--file", type=Path, default=default_file)
    listing.add_argument("--category", help="category prefix, e.g. identity")
    listing.set_defaults(run=_list)
    show = actions.add_parser("show", help="print one scenario as JSON")
    show.add_argument("id")
    show.add_argument("--file", type=Path, default=default_file)
    show.set_defaults(run=_show)
    explained = actions.add_parser("explain", help="tell one scenario as a story")
    explained.add_argument("id")
    explained.add_argument("--file", type=Path, default=default_file)
    explained.set_defaults(run=_explain)
    schema = actions.add_parser("schema", help="print the scenario JSON Schema")
    schema.set_defaults(run=_schema)

    default_db = Path("data") / DB_NAME
    legacy = commands.add_parser("legacy", help="look at the legacy database through the adapter")
    legacy_actions = legacy.add_subparsers(dest="action", required=True)
    inspect = legacy_actions.add_parser("inspect", help="summarise the data problems found")
    inspect.add_argument("--db", type=Path, default=default_db)
    inspect.set_defaults(run=_legacy_inspect)

    identity = commands.add_parser("identity", help="identity resolution")
    identity_actions = identity.add_subparsers(dest="action", required=True)
    resolve = identity_actions.add_parser("resolve", help="who is calling, given some evidence")
    resolve.add_argument("--db", type=Path, default=default_db)
    resolve.add_argument("--number", help="calling number in E.164, e.g. +34600111222")
    resolve.add_argument("--name", help="the caller's name as heard")
    resolve.add_argument("--name-verified", action="store_true", help="the name was spelled")
    resolve.add_argument("--pet", help="the pet's name as heard")
    resolve.add_argument("--pet-verified", action="store_true", help="the pet name was confirmed")
    resolve.add_argument("--town", help="where the caller says they live, as heard")
    resolve.set_defaults(run=_identity_resolve)
    evaluate = identity_actions.add_parser("eval", help="measure the resolver against the truth")
    evaluate.add_argument("--data", type=Path, default=Path("data"))
    evaluate.add_argument("--strangers", type=int, default=2000,
                          help="non-client callers in the sweep; -1 skips the sweep")
    evaluate.set_defaults(run=_identity_eval)

    chat = commands.add_parser("chat", help="talk to the agent in text, as if on the phone")
    chat.add_argument("--data", type=Path, default=Path("data"))
    chat.add_argument("--number", help="calling number in E.164; leave out for a hidden number")
    chat.add_argument("--now", type=datetime.fromisoformat,
                      help="when the call happens, e.g. 2026-11-03T10:15 (default: now)")
    chat.add_argument("--provider", choices=PROVIDERS,
                      help="LLM provider (default: the model's, else anthropic)")
    chat.add_argument("--model", help="model id, e.g. claude-sonnet-5-5 or gemini-flash-latest")
    chat.add_argument("--verbose", action="store_true", help="show the tool calls")
    chat.set_defaults(run=_chat)

    latency = commands.add_parser(
        "latency", help="play one fixed call against several models and time the answers"
    )
    latency.add_argument("--data", type=Path, default=Path("data"))
    latency.add_argument("--provider", choices=PROVIDERS,
                         help="LLM provider (default: each model's own)")
    latency.add_argument("--models", default=",".join(PRICES),
                         help="comma-separated model ids, from any provider")
    latency.set_defaults(run=_latency)

    args = parser.parse_args(argv)
    return args.run(args)


if __name__ == "__main__":
    raise SystemExit(main())
