"""Play a scenario as a whole phone call: a simulated caller against the real agent.

The agent side is exactly what answers a real call: the same prompt, tools, resolver and
agenda, through `FrontDeskAgent`. Only three things are staged: the clock (the scenario's),
the appointments that exist before the call, and the caller.
"""

from __future__ import annotations

from ..agent import FrontDeskAgent
from ..kb import KnowledgeBase
from ..legacy.models import Clinic
from ..llm import LLMClient, Usage
from ..scenario import Scenario
from ..scheduling import Appointment, SqliteAgenda
from .caller import SimulatedCaller
from .record import Booking, CallRecord, Exchange, MessageLeft, Tokens, ToolUse
from .speech import SpeechChannel
from .truth import Truth

# A call that is still going after this many caller lines is not going to end by itself.
MAX_EXCHANGES = 24
# A caller who says goodbye while the agent is still asking for something (the phone number
# the booking needs) is kept on the line to answer it, this many times at most. A real
# person does not hang up on a question; a simulated one sometimes does, and the agent
# would be blamed for a booking it was not allowed to finish.
MAX_HELD_ON_THE_LINE = 2


def _agenda(scenario: Scenario, kb: KnowledgeBase, truth: Truth) -> SqliteAgenda:
    agenda = SqliteAgenda(kb, lambda: scenario.clock)
    for booked in scenario.fixtures.appointments:
        agenda.add(Appointment(
            appointment_id=booked.appointment_id,
            start=booked.start,
            reason=booked.reason,
            pet_name=truth.pet_name(booked.pet_id),
            client_code=truth.client_code(booked.client_id),
            animal_code=truth.pet_code(booked.pet_id),
            contact_name=None,
            contact_phone=None,
            verified=True,
        ))
    return agenda


def play(
    scenario: Scenario,
    *,
    agent_llm: LLMClient,
    caller_llm: LLMClient,
    clinic: Clinic,
    kb: KnowledgeBase,
    truth: Truth,
    rep: int = 0,
    agent_model: str = "",
    caller_model: str = "",
    caller_style: str = "forthcoming",
) -> CallRecord:
    """Play one call to the end. A failure of either model is recorded, not raised."""
    agenda = _agenda(scenario, kb, truth)
    agent = FrontDeskAgent(agent_llm, clinic, kb, agenda, lambda: scenario.clock)
    call = agent.start_call(scenario.call.caller_number)
    channel = SpeechChannel(scenario.speech)
    exchanges: list[Exchange] = []
    agent_usage, caller_usage = Usage(), Usage()
    ended, error, held = "turn_limit", None, 0
    try:
        caller = SimulatedCaller(caller_llm, scenario, truth, caller_style)
        agent_said = call.greeting
        for _ in range(MAX_EXCHANGES):
            line = caller.reply(agent_said)
            caller_usage += line.usage
            if line.text:
                heard = channel.hear(line.text)
                turn = call.say(heard)
                agent_usage += turn.usage
                agent_said = turn.text
                exchanges.append(Exchange(
                    said=line.text,
                    heard=heard,
                    answer=turn.text,
                    tools=[ToolUse(name=e.name, arguments=e.arguments, result=e.result,
                                   is_error=e.is_error) for e in turn.events],
                    confirmed=call.session.client is not None,
                    first_words=turn.first_words,
                    seconds=turn.seconds,
                    requests=list(turn.latencies),
                ))
            if line.hang_up:
                asked_something = bool(line.text) and "?" in agent_said
                if asked_something and held < MAX_HELD_ON_THE_LINE:
                    held += 1
                    continue
                ended = "hung_up" if line.hang_up == "done" else "gave_up"
                break
            if not line.text:
                raise RuntimeError("the simulated caller said nothing")
    except Exception as failure:  # one broken call must not stop a paid run
        ended, error = "error", f"{type(failure).__name__}: {failure}"
    session = call.session
    return CallRecord(
        scenario_id=scenario.id,
        rep=rep,
        agent_model=agent_model,
        caller_model=caller_model,
        caller_style=caller_style,
        greeting=call.greeting,
        exchanges=exchanges,
        ended=ended,
        error=error,
        confirmed_client_code=session.client.code if session.client else None,
        appointments=[
            Booking(appointment_id=a.appointment_id, start=a.start, reason=a.reason,
                    pet_name=a.pet_name, client_code=a.client_code, animal_code=a.animal_code,
                    contact_name=a.contact_name, contact_phone=a.contact_phone,
                    verified=a.verified, status=a.status)
            for a in agenda.all()
        ],
        messages=[MessageLeft(text=m.text, contact_name=m.contact_name,
                              contact_phone=m.contact_phone, client_code=m.client_code)
                  for m in session.messages],
        agent_tokens=Tokens.of(agent_usage),
        caller_tokens=Tokens.of(caller_usage),
    )
