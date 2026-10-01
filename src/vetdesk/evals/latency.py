"""How long callers wait: the same short call played against several models.

On the phone, the time the model takes is silence. This plays one fixed call (a general
question, a request for an appointment, identification by name, pet and town, a booking)
against each model and reports how long each answer took and what the call cost.

The caller's lines are fixed, so the conversation is not always natural: a model may ask
something the next line does not answer. That is fine here. What is measured is waiting
time per request and per answer, not the quality of the conversation.
"""

from __future__ import annotations

import statistics
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from ..agent import FrontDeskAgent
from ..kb import KnowledgeBase
from ..legacy.models import Clinic
from ..llm import LLMClient, Usage
from ..scenario import Scenario
from ..scheduling import SqliteAgenda

# US dollars per million tokens (input, output), first-party API prices as of 2026-09.
# Cached input is billed at a tenth of the input price; writing the cache at 1.25 times.
PRICES = {
    "claude-opus-5-5": (4.0, 20.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-4-5": (1.0, 5.0),
}


@dataclass(frozen=True)
class CallTiming:
    model: str
    answers: tuple[float, ...]  # seconds the caller waited for each answer
    requests: tuple[float, ...]  # seconds each request to the model took
    usage: Usage
    confirmed: bool
    booked: bool

    @property
    def cost(self) -> float | None:
        if self.model not in PRICES:
            return None
        price_in, price_out = PRICES[self.model]
        u = self.usage
        tokens_in = u.input_tokens + 0.1 * u.cache_read_tokens + 1.25 * u.cache_write_tokens
        return (tokens_in * price_in + u.output_tokens * price_out) / 1_000_000


def caller_lines(scenario: Scenario) -> list[str]:
    """What the caller says, in order, whatever the agent answers."""
    caller = scenario.caller
    pet = caller.pets[0].name
    return [
        "Hola, ¿a qué hora abrís los sábados?",
        f"Quería pedir cita para {pet}, que se rasca mucho. La semana que viene por la mañana.",
        f"Me llamo {caller.says_name}.",
        f"Vivo en {caller.town}.",
        "La primera hora que me has dicho me va bien.",
        "Nada más, gracias.",
    ]


def time_call(
    model: str,
    llm: LLMClient,
    clinic: Clinic,
    kb: KnowledgeBase,
    scenario: Scenario,
    report: Callable[[str], None] = lambda line: None,
) -> CallTiming:
    def now() -> datetime:
        return scenario.clock

    agent = FrontDeskAgent(llm, clinic, kb, SqliteAgenda(kb, now), now)
    call = agent.start_call(scenario.call.caller_number)
    answers, requests, usage = [], [], Usage()
    for line in caller_lines(scenario):
        turn = call.say(line)
        answers.append(turn.seconds)
        requests.extend(turn.latencies)
        usage += turn.usage
        report(f"  caller > {line}\n  agent  > {turn.text}\n           ({turn.seconds:.1f} s)")
    booked = any(e.name == "book_appointment" and not e.is_error for e in call.session.events)
    return CallTiming(model, tuple(answers), tuple(requests), usage,
                      call.session.client is not None, booked)


def format_timings(timings: list[CallTiming]) -> str:
    lines = [
        f"{'model':20} {'answer: median':>15} {'slowest':>8} {'per request':>12} "
        f"{'requests':>9} {'cost':>8}  outcome",
    ]
    for t in timings:
        cost = f"${t.cost:.3f}" if t.cost is not None else "?"
        outcome = ("booked" if t.booked else "no booking") + \
            (", caller confirmed" if t.confirmed else ", caller not confirmed")
        lines.append(
            f"{t.model:20} {statistics.median(t.answers):13.1f} s {max(t.answers):6.1f} s "
            f"{statistics.median(t.requests):10.1f} s {len(t.requests):9d} {cost:>8}  {outcome}"
        )
    return "\n".join(lines)
