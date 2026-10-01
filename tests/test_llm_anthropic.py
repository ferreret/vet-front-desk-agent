"""The Anthropic adapter, against a stand-in for the SDK client: request shape and history.

These tests never touch the network. They pin down what the adapter sends and how it keeps
the conversation; they cannot tell whether the live API accepts it.
"""

from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from vetdesk.agent import SPECS
from vetdesk.llm import LLMError, ToolResult, create_client
from vetdesk.llm.anthropic_client import FALLBACK_BETA, AnthropicClient


def _response(*blocks, stop_reason="end_turn"):
    usage = SimpleNamespace(input_tokens=1500, output_tokens=40, cache_read_input_tokens=1200,
                            cache_creation_input_tokens=0)
    return SimpleNamespace(content=list(blocks), stop_reason=stop_reason, usage=usage)


def _text(text):
    return SimpleNamespace(type="text", text=text)


def _tool_use(call_id, name, **arguments):
    return SimpleNamespace(type="tool_use", id=call_id, name=name, input=arguments)


THINKING = SimpleNamespace(type="thinking", thinking="", signature="opaque")


class _Sdk:
    """Records every request; answers with canned responses or raises a canned error."""

    def __init__(self, *responses):
        self._responses = list(responses)
        self.requests: list[tuple[str, dict]] = []
        self.messages = SimpleNamespace(create=lambda **kw: self._create("messages", kw))
        self.beta = SimpleNamespace(
            messages=SimpleNamespace(create=lambda **kw: self._create("beta", kw))
        )

    def _create(self, endpoint, params):
        self.requests.append((endpoint, {**params, "messages": list(params["messages"])}))
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _start(sdk, model="claude-opus-5-5", **options):
    client = AnthropicClient(model, client=sdk, **options)
    return client.start("SYSTEM", "CONTEXT", SPECS)


def test_request_for_the_default_model():
    sdk = _Sdk(_response(_text("Hola.")))
    reply = _start(sdk).send_user("Hola")
    endpoint, request = sdk.requests[0]
    assert endpoint == "beta"
    assert request["betas"] == [FALLBACK_BETA] and request["fallbacks"] == "default"
    assert request["model"] == "claude-opus-5-5"
    assert request["output_config"] == {"effort": "low"}
    assert "thinking" not in request and "tool_choice" not in request
    assert request["messages"] == [{"role": "user", "content": "Hola"}]
    assert (reply.text, reply.stop, reply.tool_calls) == ("Hola.", "end", ())
    assert (reply.usage.input_tokens, reply.usage.cache_read_tokens) == (1500, 1200)


def test_the_stable_prompt_is_cached_and_the_call_context_is_not():
    sdk = _Sdk(_response(_text("Hola.")))
    _start(sdk).send_user("Hola")
    request = sdk.requests[0][1]
    assert request["system"] == [
        {"type": "text", "text": "SYSTEM", "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": "CONTEXT"},
    ]
    assert request["cache_control"] == {"type": "ephemeral"}


def test_tools_are_sent_strict_and_in_a_fixed_order():
    sdk = _Sdk(_response(_text("Hola.")), _response(_text("Hola.")))
    _start(sdk).send_user("Hola")
    _start(sdk).send_user("Hola")
    tools = sdk.requests[0][1]["tools"]
    assert [tool["name"] for tool in tools] == [spec.name for spec in SPECS]
    assert all(tool["strict"] is True for tool in tools)
    assert all(tool["input_schema"]["additionalProperties"] is False for tool in tools)
    assert tools == sdk.requests[1][1]["tools"]  # byte-identical prefix, or the cache misses


def test_a_model_without_effort_or_fallback_support():
    sdk = _Sdk(_response(_text("Hola.")))
    _start(sdk, "claude-haiku-4-5").send_user("Hola")
    endpoint, request = sdk.requests[0]
    assert endpoint == "messages"
    assert "output_config" not in request and "fallbacks" not in request


def test_fallbacks_can_be_switched_off():
    sdk = _Sdk(_response(_text("Hola.")))
    _start(sdk, fallbacks=False, effort="medium").send_user("Hola")
    endpoint, request = sdk.requests[0]
    assert endpoint == "messages" and request["output_config"] == {"effort": "medium"}


def test_a_tool_round_keeps_the_history_intact():
    first = _response(THINKING, _text("Un momento."),
                      _tool_use("t1", "get_pets"),
                      _tool_use("t2", "list_appointments"), stop_reason="tool_use")
    sdk = _Sdk(first, _response(_text("Listo.")))
    conversation = _start(sdk)
    reply = conversation.send_user("¿Qué citas tengo?")
    assert reply.stop == "tool_calls" and reply.text == "Un momento."
    assert [(c.id, c.name, c.arguments) for c in reply.tool_calls] == [
        ("t1", "get_pets", {}), ("t2", "list_appointments", {}),
    ]
    conversation.send_tool_results([
        ToolResult("t1", '{"pets": []}'), ToolResult("t2", '{"error": "no"}', True),
    ])
    messages = sdk.requests[1][1]["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    # The model's turn goes back exactly as it came, reasoning block included.
    assert messages[1]["content"] is first.content
    assert messages[2]["content"] == [  # every result of the round, in one message
        {"type": "tool_result", "tool_use_id": "t1", "content": '{"pets": []}',
         "is_error": False},
        {"type": "tool_result", "tool_use_id": "t2", "content": '{"error": "no"}',
         "is_error": True},
    ]


def test_a_refusal_is_reported_and_not_kept_in_the_history():
    sdk = _Sdk(_response(stop_reason="refusal"), _response(_text("Dígame.")))
    conversation = _start(sdk)
    assert conversation.send_user("...").stop == "refusal"
    conversation.send_user("Hola")
    assert [m["role"] for m in sdk.requests[1][1]["messages"]] == ["user", "user"]


def test_a_cut_off_reply_is_reported():
    sdk = _Sdk(_response(_text("Le cuento que"), stop_reason="max_tokens"))
    assert _start(sdk).send_user("Hola").stop == "max_tokens"


@pytest.mark.parametrize("status,retryable", [(529, True), (500, True), (400, False), (401, False)])
def test_provider_errors_become_llm_errors(status, retryable):
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx2.Response(status, request=request)
    error = anthropic.APIStatusError("boom", response=response, body=None)
    sdk = _Sdk(error, _response(_text("Hola.")))
    conversation = _start(sdk)
    with pytest.raises(LLMError) as caught:
        conversation.send_user("Hola")
    assert caught.value.retryable is retryable
    # The failed turn left no trace: sending it again gives a well-formed conversation.
    conversation.send_user("Hola")
    assert sdk.requests[1][1]["messages"] == [{"role": "user", "content": "Hola"}]


def test_a_dropped_connection_is_retryable():
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    sdk = _Sdk(anthropic.APIConnectionError(request=request))
    with pytest.raises(LLMError) as caught:
        _start(sdk).send_user("Hola")
    assert caught.value.retryable


def test_unknown_providers_are_named_as_such():
    with pytest.raises(LLMError, match="unknown provider 'acme'; available: anthropic"):
        create_client("acme")


def test_a_key_without_a_workspace_sends_the_workspace_header(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "not-a-real-key")
    monkeypatch.delenv("ANTHROPIC_WORKSPACE_ID", raising=False)
    assert "anthropic-workspace-id" not in AnthropicClient()._client.default_headers
    monkeypatch.setenv("ANTHROPIC_WORKSPACE_ID", " wrkspc_example ")
    headers = AnthropicClient()._client.default_headers
    assert headers["anthropic-workspace-id"] == "wrkspc_example"
