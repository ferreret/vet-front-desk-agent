"""The chat-completions adapter (OpenAI, and other models through the Requesty router),
against a stand-in for the SDK client: request shape, streaming and history.

No network: whether the live APIs accept the requests is not tested here.
"""

from types import SimpleNamespace

import httpx
import openai
import pytest

from vetdesk.agent import SPECS
from vetdesk.llm import LLMError, ToolResult, create_client, provider_of
from vetdesk.llm.openai_client import OpenAICompatClient

USAGE = SimpleNamespace(prompt_tokens=1500, completion_tokens=45,
                        prompt_tokens_details=SimpleNamespace(cached_tokens=1200))


def _chunk(content=None, calls=None, finish=None, refusal=None):
    delta = SimpleNamespace(content=content, tool_calls=calls, refusal=refusal)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason=finish)],
                           usage=None)


def _call(index, call_id=None, name=None, arguments=None):
    return SimpleNamespace(index=index, id=call_id,
                           function=SimpleNamespace(name=name, arguments=arguments))


def _reply(*chunks):
    """A streamed reply: its chunks, then the one that only carries the token counts."""
    return [*chunks, SimpleNamespace(choices=[], usage=USAGE)]


def _status_error(kind, code, message="boom"):
    response = httpx.Response(code, request=httpx.Request("POST", "https://example.test"))
    return kind(message, response=response, body=None)


class _Sdk:
    def __init__(self, *replies):
        self._replies = list(replies)
        self.requests: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **params):
        self.requests.append({**params, "messages": list(params["messages"])})
        reply = self._replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return iter(reply)


def _start(sdk, model="gpt-6-luna", provider="openai"):
    client = OpenAICompatClient(model, provider=provider, client=sdk)
    return client.start("SYSTEM", "CONTEXT", SPECS)


def test_request_shape():
    sdk = _Sdk(_reply(_chunk("Hola."), _chunk(finish="stop")))
    reply = _start(sdk).send_user("Hola")
    request = sdk.requests[0]
    assert request["model"] == "gpt-6-luna" and request["stream"] is True
    assert request["stream_options"] == {"include_usage": True}
    assert request["reasoning_effort"] == "none" and request["max_completion_tokens"] == 4096
    assert request["messages"] == [{"role": "system", "content": "SYSTEM\n\nCONTEXT"},
                                   {"role": "user", "content": "Hola"}]
    functions = [tool["function"] for tool in request["tools"]]
    assert [f["name"] for f in functions] == [spec.name for spec in SPECS]
    assert functions[0]["parameters"] == SPECS[0].parameters and functions[0]["strict"] is True
    assert (reply.text, reply.stop, reply.tool_calls) == ("Hola.", "end", ())
    usage = reply.usage
    assert (usage.input_tokens, usage.cache_read_tokens, usage.output_tokens) == (300, 1200, 45)


def test_the_router_is_asked_in_its_own_terms():
    sdk = _Sdk(_reply(_chunk("Hola."), _chunk(finish="stop")))
    _start(sdk, "zai/glm-5.3-flash", "requesty").send_user("Hola")
    request = sdk.requests[0]
    assert request["model"] == "zai/glm-5.3-flash" and request["max_tokens"] == 4096
    assert "max_completion_tokens" not in request
    assert "strict" not in request["tools"][0]["function"]


def test_text_is_handed_over_as_it_arrives_and_calls_are_put_back_together():
    first = _reply(
        _chunk("Un mo"), _chunk("mento."),
        _chunk(calls=[_call(0, "id-1", "get_pets", "")]),
        _chunk(calls=[_call(1, "id-2", "get_availability", '{"date_from": "2026-11-09", ')]),
        _chunk(calls=[_call(1, arguments='"date_to": "2026-11-13"}')]),
        _chunk(finish="tool_calls"),
    )
    sdk = _Sdk(first, _reply(_chunk("Listo."), _chunk(finish="stop")))
    conversation, heard = _start(sdk), []
    reply = conversation.send_user("¿Qué citas tengo?", heard.append)
    assert heard == ["Un mo", "mento."] and reply.text == "Un momento."
    assert reply.stop == "tool_calls"
    assert [(c.id, c.name, c.arguments) for c in reply.tool_calls] == [
        ("id-1", "get_pets", {}),
        ("id-2", "get_availability", {"date_from": "2026-11-09", "date_to": "2026-11-13"}),
    ]
    conversation.send_tool_results([ToolResult("id-1", '{"pets": []}'),
                                    ToolResult("id-2", '{"error": "no"}', True)])
    messages = sdk.requests[1]["messages"]
    assert [m["role"] for m in messages] == ["system", "user", "assistant", "tool", "tool"]
    said = messages[2]
    assert said["content"] == "Un momento."
    assert [(c["id"], c["function"]["name"]) for c in said["tool_calls"]] == [
        ("id-1", "get_pets"), ("id-2", "get_availability")]
    assert said["tool_calls"][0]["function"]["arguments"] == "{}"
    assert messages[3] == {"role": "tool", "tool_call_id": "id-1", "content": '{"pets": []}'}


def test_a_refused_effort_is_stepped_down_and_remembered():
    refusal = _status_error(openai.BadRequestError, 400, "Unsupported value: 'none'")
    sdk = _Sdk(refusal, _reply(_chunk("Hola."), _chunk(finish="stop")),
               _reply(_chunk("Hola."), _chunk(finish="stop")))
    client = OpenAICompatClient("gpt-6-luna", client=sdk)
    assert client.start("SYSTEM", "CONTEXT", SPECS).send_user("Hola").text == "Hola."
    assert [r["reasoning_effort"] for r in sdk.requests] == ["none", "minimal"]
    client.start("SYSTEM", "CONTEXT", SPECS).send_user("Hola")
    assert sdk.requests[2]["reasoning_effort"] == "minimal"  # no second refusal


def test_a_400_that_no_effort_fixes_is_reported_as_it_came():
    sdk = _Sdk(*[_status_error(openai.BadRequestError, 400, text)
                 for text in ("boom", "later", "later", "later")],
               _reply(_chunk("Hola."), _chunk(finish="stop")))
    conversation = _start(sdk)
    with pytest.raises(LLMError) as caught:
        conversation.send_user("Hola")
    assert not caught.value.retryable and "boom" in str(caught.value)
    assert "reasoning_effort" not in sdk.requests[3]  # the last thing tried: no effort at all
    conversation.send_user("Hola")  # the failed turn left no trace, and nothing was unlearnt
    assert len(sdk.requests[4]["messages"]) == 2
    assert sdk.requests[4]["reasoning_effort"] == "none"


def test_blocked_and_cut_off_replies():
    blocked = _reply(_chunk(refusal="No puedo ayudar con eso."), _chunk(finish="stop"))
    conversation = _start(_Sdk(blocked, _reply(_chunk("Hola."), _chunk(finish="stop"))))
    assert conversation.send_user("x").stop == "refusal"
    filtered = _reply(_chunk(finish="content_filter"))
    assert _start(_Sdk(filtered)).send_user("x").stop == "refusal"
    cut = _reply(_chunk("Le cuento que"), _chunk(finish="length"))
    assert _start(_Sdk(cut)).send_user("x").stop == "max_tokens"


@pytest.mark.parametrize("kind,code,retryable", [
    (openai.InternalServerError, 503, True),
    (openai.RateLimitError, 429, True),
    (openai.NotFoundError, 404, False),
])
def test_provider_errors_become_llm_errors(kind, code, retryable):
    sdk = _Sdk(_status_error(kind, code), _reply(_chunk("Hola."), _chunk(finish="stop")))
    conversation = _start(sdk)
    with pytest.raises(LLMError) as caught:
        conversation.send_user("Hola")
    assert caught.value.retryable is retryable
    conversation.send_user("Hola")  # the failed turn left no trace
    assert len(sdk.requests[1]["messages"]) == 2


def test_a_dropped_connection_is_retryable():
    down = openai.APIConnectionError(request=httpx.Request("POST", "https://example.test"))
    with pytest.raises(LLMError) as caught:
        _start(_Sdk(down)).send_user("Hola")
    assert caught.value.retryable


def test_a_missing_key_is_explained(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("REQUESTY_API_KEY", raising=False)
    with pytest.raises(LLMError, match="OPENAI_API_KEY"):
        create_client(model="gpt-6-luna")
    with pytest.raises(LLMError, match="REQUESTY_API_KEY"):
        create_client(model="zai/glm-5.3-flash")
    with pytest.raises(LLMError, match="model id"):
        create_client("requesty")


def test_the_model_id_picks_the_provider():
    assert provider_of("gpt-6-luna") == "openai"
    assert provider_of("zai/glm-5.3-flash") == "requesty"
    assert provider_of("openai/gpt-6-luna") == "requesty"  # through the router, as named
    assert provider_of("claude-sonnet-5-5") == "anthropic"
