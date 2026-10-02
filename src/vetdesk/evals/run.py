"""Run the evaluation: play every scenario, judge every call, keep everything.

A run lives in a directory. Calls and judgements are appended as they finish, so a run
that stops half-way (no credit left, a closed laptop) picks up where it stopped: calls
already played are not played again, and a call that broke is retried.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from ..kb import KnowledgeBase
from ..scenario import Scenario
from .record import CallRecord, Judgement, Verdict
from .scoring import score
from .truth import Truth

CALLS_NAME = "calls.jsonl"
JUDGEMENTS_NAME = "judgements.jsonl"
INFO_NAME = "run.json"

# This many calls broken in a row means something is wrong with the account or the
# network, not with a call. Stop instead of burning through the rest.
MAX_ERRORS_IN_A_ROW = 5

Key = tuple[str, int]  # scenario id, repetition


@dataclass(frozen=True)
class Models:
    agent: str
    caller: str
    judge: str | None


class RunStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self.calls: dict[Key, CallRecord] = {}
        self.judgements: dict[Key, Judgement] = {}
        # A later line for the same call replaces an earlier one: a retried call, or a call
        # judged again.
        for line in self._lines(CALLS_NAME):
            record = CallRecord.model_validate_json(line)
            self.calls[record.scenario_id, record.rep] = record
        for line in self._lines(JUDGEMENTS_NAME):
            judgement = Judgement.model_validate_json(line)
            self.judgements[judgement.scenario_id, judgement.rep] = judgement

    def _lines(self, name: str) -> list[str]:
        file = self.path / name
        if not file.exists():
            return []
        return [line for line in file.read_text(encoding="utf-8").splitlines() if line.strip()]

    def _append(self, name: str, line: str) -> None:
        self.path.mkdir(parents=True, exist_ok=True)
        with (self.path / name).open("a", encoding="utf-8") as file:
            file.write(line + "\n")

    def models(self) -> Models | None:
        file = self.path / INFO_NAME
        if not file.exists():
            return None
        return Models(**json.loads(file.read_text(encoding="utf-8"))["models"])

    def write_info(self, models: Models, extra: dict) -> None:
        self.path.mkdir(parents=True, exist_ok=True)
        info = {"models": {"agent": models.agent, "caller": models.caller,
                           "judge": models.judge}, **extra}
        (self.path / INFO_NAME).write_text(json.dumps(info, indent=1) + "\n", encoding="utf-8")

    def add_call(self, record: CallRecord) -> None:
        with self._lock:
            key = (record.scenario_id, record.rep)
            self.calls[key] = record
            self.judgements.pop(key, None)  # a judgement of an earlier attempt no longer holds
            self._append(CALLS_NAME, record.model_dump_json())

    def add_judgement(self, judgement: Judgement) -> None:
        with self._lock:
            self.judgements[judgement.scenario_id, judgement.rep] = judgement
            self._append(JUDGEMENTS_NAME, judgement.model_dump_json())

    def played(self, key: Key) -> bool:
        return key in self.calls and self.calls[key].ended != "error"

    def judged(self, key: Key) -> bool:
        return key in self.judgements


def run(
    scenarios: list[Scenario],
    store: RunStore,
    play_call: Callable[[Scenario, int], CallRecord],
    judge_call: Callable[[CallRecord, Scenario], Judgement] | None,
    *,
    reps: int = 1,
    workers: int = 4,
    report: Callable[[str], None] = lambda line: None,
) -> bool:
    """Play and judge whatever the run is still missing. False if it had to stop early."""
    lock = threading.Lock()
    errors_in_a_row = 0
    stop = threading.Event()

    def one(scenario: Scenario, rep: int) -> None:
        nonlocal errors_in_a_row
        key = (scenario.id, rep)
        if stop.is_set():
            return
        if not store.played(key):
            record = play_call(scenario, rep)
            store.add_call(record)
            with lock:
                errors_in_a_row = errors_in_a_row + 1 if record.ended == "error" else 0
                if errors_in_a_row >= MAX_ERRORS_IN_A_ROW:
                    stop.set()
            if record.ended == "error":
                report(f"{scenario.id}  error: {record.error}")
                return
            report(f"{scenario.id}  played: {len(record.exchanges)} turns, {record.ended}")
        if judge_call is not None and not store.judged(key):
            try:
                store.add_judgement(judge_call(store.calls[key], scenario))
            except Exception as failure:  # the call is kept; it can be judged on the next run
                report(f"{scenario.id}  not judged: {type(failure).__name__}: {failure}")

    jobs = [(scenario, rep) for rep in range(reps) for scenario in scenarios]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for future in [pool.submit(one, scenario, rep) for scenario, rep in jobs]:
            future.result()
    return not stop.is_set()


def verdicts(
    scenarios: list[Scenario], store: RunStore, truth: Truth, kb: KnowledgeBase
) -> list[Verdict]:
    """Score every call of a run that belongs to one of `scenarios`. Costs nothing."""
    by_id = {scenario.id: scenario for scenario in scenarios}
    return [
        score(by_id[scenario_id], record, truth, kb, store.judgements.get((scenario_id, rep)))
        for (scenario_id, rep), record in sorted(store.calls.items())
        if scenario_id in by_id
    ]
