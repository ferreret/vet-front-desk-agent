"""Command line: `vetdesk generate` builds the synthetic clinic and its call scenarios."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from .explain import Labels, explain
from .scenario import Scenario, dump_jsonl, load_jsonl
from .synth import GeneratorConfig, generate_world
from .synth.legacy_db import ForeignDatabaseError, defect_counts, export_truth, write_legacy_db
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

    args = parser.parse_args(argv)
    return args.run(args)


if __name__ == "__main__":
    raise SystemExit(main())
