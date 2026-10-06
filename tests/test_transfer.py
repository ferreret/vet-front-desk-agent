"""Putting a call through to a person: only when one can take it, and never just said."""

import asyncio
import json
from datetime import datetime

import pytest
from aiohttp.test_utils import TestClient, TestServer

from vetdesk.agent import FrontDeskAgent
from vetdesk.agent.prompt import PHRASES, THROUGH, system_prompt
from vetdesk.agent.tools import SPECS, TRANSFER
from vetdesk.kb import load_kb
from vetdesk.language import SPOKEN
from vetdesk.llm import Reply, ToolCall
from vetdesk.llm.scripted import ScriptedClient
from vetdesk.scheduling import SqliteAgenda
from vetdesk.voice.endpoint import NOT_PUT_THROUGH, Switchboard, build_app

OPEN, CLOSED = datetime(2026, 11, 3, 10, 15), datetime(2026, 11, 8, 3, 20)
ASK = ToolCall("t", "transfer_to_reception", {"summary": "Marta Soler, por una factura."})
PLATFORM = [{"type": "function", "function": {"name": "transfer_to_number",
                                              "parameters": {"type": "object"}}}]


@pytest.fixture(scope="module")
def kb():
    return load_kb()


def _agent(model, clinic, kb, now):
    return FrontDeskAgent(model, clinic, kb, SqliteAgenda(kb, lambda: now), lambda: now)


def test_the_tool_is_there_only_while_somebody_can_take_the_call(clinic, kb):
    for now, able, offered in ((OPEN, True, True), (CLOSED, True, False), (OPEN, False, False)):
        model = ScriptedClient([])
        _agent(model, clinic, kb, now).start_call(None, able)
        assert (TRANSFER in model.transcript.tools) is offered
        assert len(model.transcript.tools) == len(SPECS) + offered
        told = "A caller can be put through" if offered else "No caller can be put through"
        assert told in model.transcript.context
    assert set(THROUGH) == set(SPOKEN)


def test_what_the_caller_hears_reaches_the_model_only_from_the_tool(clinic, kb):
    """"Le paso con recepción" with nothing behind it is what the 2025 pilot said. The words
    are in no instruction and no set phrase: the tool that transfers hands them over."""
    assert not any(phrase in system_prompt(kb) for phrase in THROUGH.values())
    assert not any(through in " ".join(phrases)
                   for through in THROUGH.values() for phrases in PHRASES.values())
    model = ScriptedClient([Reply("", (ASK,), "tool_calls"), Reply(THROUGH["es"])])
    call = _agent(model, clinic, kb, OPEN).start_call("+34600111222", True)
    assert not any(through in model.transcript.context for through in THROUGH.values())
    turn = call.say("Quería hablar con alguien por una factura.")
    result = turn.events[0].result
    assert result["status"] == "putting_through" and result["say"] == THROUGH["es"]
    assert call.session.transfer == "Marta Soler, por una factura."
    (notice,) = call.session.notices
    assert notice.kind == "put_through" and "Marta Soler, por una factura." in notice.text
    assert notice.html.startswith("📲 <b>LLAMADA PASADA A RECEPCIÓN</b>")


def test_a_model_that_asks_for_a_transfer_it_does_not_have_is_told_no(clinic, kb):
    model = ScriptedClient([Reply("", (ASK,), "tool_calls"), Reply("Le tomo nota.")])
    call = _agent(model, clinic, kb, CLOSED).start_call(None, True)
    turn = call.say("Quería hablar con alguien.")
    assert turn.events[0].is_error and "take a message" in turn.events[0].result["error"]
    assert call.session.transfer is None and call.session.notices == []


def _server(model, clinic, kb, now, transfer_to="+34600999888"):
    agent = _agent(model, clinic, kb, now)
    return build_app(Switchboard(agent.start_call), "key", transfer_to=transfer_to)


def _ask(app, *requests, tools=PLATFORM):
    async def run():
        async with TestClient(TestServer(app)) as client:
            answers = []
            for messages in requests:
                response = await client.post(
                    "/v1/chat/completions", headers={"Authorization": "Bearer key"},
                    json={"model": "x", "messages": messages, "tools": tools})
                answers.append(await response.text())
            return answers

    return asyncio.run(run())


def _read(stream):
    chunks = [json.loads(line[6:]) for line in stream.splitlines()
              if line.startswith("data: ") and line != "data: [DONE]"]
    said = "".join(chunk["choices"][0]["delta"].get("content") or "" for chunk in chunks)
    calls = [call for chunk in chunks
             for call in chunk["choices"][0]["delta"].get("tool_calls", [])]
    asked = {"name": calls[0]["function"]["name"],
             **json.loads(calls[0]["function"]["arguments"])} if calls else None
    return said, asked


SYSTEM = {"role": "system", "content": "vetdesk-conversation: c1\nvetdesk-caller: +34600111222"}
WANTS = [SYSTEM, {"role": "user", "content": "Quería hablar con alguien por una factura."}]


def test_the_platform_is_told_to_put_the_call_through_and_says_the_words_itself(clinic, kb):
    model = ScriptedClient([Reply("", (ASK,), "tool_calls"), Reply(THROUGH["es"])])
    (stream,) = _ask(_server(model, clinic, kb, OPEN), WANTS)
    said, asked = _read(stream)
    assert said == ""  # not said by us as well: the platform says it while it dials
    assert asked == {"name": "transfer_to_number", "transfer_number": "+34600999888",
                     "reason": "the caller asked to speak to a person",
                     "client_message": THROUGH["es"],
                     "agent_message": "Marta Soler, por una factura."}


def test_when_nobody_picks_up_the_caller_is_told_and_a_message_is_offered(clinic, kb):
    model = ScriptedClient([Reply("", (ASK,), "tool_calls"), Reply(THROUGH["es"]),
                            Reply("No contestan. ¿Le tomo nota y le llaman?")])
    failed = [*WANTS, {"role": "assistant", "content": None, "tool_calls": [{"id": "c"}]},
              {"role": "tool", "tool_call_id": "c",
               "content": '{"result_type":"transfer_to_number_error","status":"error"}'}]
    _, stream = _ask(_server(model, clinic, kb, OPEN), WANTS, failed)
    said, asked = _read(stream)
    assert asked is None and said == "No contestan. ¿Le tomo nota y le llaman?"
    assert model.transcript.user_messages[-1] == NOT_PUT_THROUGH


def test_without_a_number_or_out_of_hours_the_agent_has_no_transfer(clinic, kb):
    for now, number in ((OPEN, ""), (CLOSED, "+34600999888")):
        model = ScriptedClient([Reply("No puedo pasarle la llamada, pero le tomo nota.")])
        (stream,) = _ask(_server(model, clinic, kb, now, number), WANTS)
        assert _read(stream) == ("No puedo pasarle la llamada, pero le tomo nota.", None)
        assert TRANSFER not in model.transcript.tools


def test_the_platforms_agent_holds_the_number_only_when_there_is_one(monkeypatch):
    from vetdesk.voice.elevenlabs_agent import config

    monkeypatch.setenv("VETDESK_TTS_VOICE", "voice")
    monkeypatch.delenv("VETDESK_TRANSFER_TO", raising=False)
    tools = config("https://example.test", "s")["conversation_config"]["agent"]["prompt"]
    assert "transfer_to_number" not in tools["built_in_tools"]
    monkeypatch.setenv("VETDESK_TRANSFER_TO", "+34600999888")
    tools = config("https://example.test", "s")["conversation_config"]["agent"]["prompt"]
    rule = tools["built_in_tools"]["transfer_to_number"]["params"]["transfers"][0]
    assert rule["transfer_destination"] == {"type": "phone", "phone_number": "+34600999888"}
