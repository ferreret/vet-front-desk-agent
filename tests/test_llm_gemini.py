"""The Gemini adapter, against a stand-in for the SDK client: request shape and history.

No network. Responses are built with the SDK's own types, so the adapter is exercised on
the real response model; whether the live API accepts the requests is not tested here.
"""

from types import SimpleNamespace

import httpx
import pytest
from google.genai import errors, types

from vetdesk.agent import SPECS
from vetdesk.llm import LLMError, ToolResult, create_client, provider_of
from vetdesk.llm.gemini_client import GeminiClient


def _response(*parts, finish=types.FinishReason.STOP):
    return types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(role="model", parts=list(parts)),
                                    finish_reason=finish)],
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=1500, cached_content_token_count=1200,
            candidates_token_count=40, thoughts_token_count=5,
        ),
    )


def _call(name, call_id=None, **arguments):
    return types.Part(function_call=types.FunctionCall(id=call_id, name=name, args=arguments))


class _Sdk:
    def __init__(self, *responses):
        self._responses = list(responses)
        self.requests: list[dict] = []
        self.models = SimpleNamespace(generate_content_stream=self._generate)

    def _generate(self, **params):
        """One queued item per request: an error, a whole reply, or its chunks in a list."""
        self.requests.append({**params, "contents": list(params["contents"])})
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return iter(response if isinstance(response, list) else [response])


def _start(sdk, model="gemini-flash-latest"):
    return GeminiClient(model, client=sdk).start("SYSTEM", "CONTEXT", SPECS)


def test_request_shape():
    sdk = _Sdk(_response(types.Part(text="Hola.")))
    reply = _start(sdk).send_user("Hola")
    request = sdk.requests[0]
    config = request["config"]
    assert request["model"] == "gemini-flash-latest"
    assert config.system_instruction == "SYSTEM\n\nCONTEXT"
    assert config.automatic_function_calling.disable is True
    assert config.thinking_config.thinking_level == types.ThinkingLevel.MINIMAL
    declarations = config.tools[0].function_declarations
    assert [d.name for d in declarations] == [spec.name for spec in SPECS]
    assert declarations[0].parameters_json_schema == SPECS[0].parameters
    assert [(c.role, c.parts[0].text) for c in request["contents"]] == [("user", "Hola")]
    assert (reply.text, reply.stop, reply.tool_calls) == ("Hola.", "end", ())
    usage = reply.usage
    assert (usage.input_tokens, usage.cache_read_tokens, usage.output_tokens) == (300, 1200, 45)


@pytest.mark.parametrize("model,budget,level", [
    ("gemini-2.5-flash", 0, None),
    ("gemini-3.8-flash", None, types.ThinkingLevel.MINIMAL),
])
def test_thinking_is_kept_to_the_minimum_each_model_allows(model, budget, level):
    sdk = _Sdk(_response(types.Part(text="Hola.")))
    _start(sdk, model).send_user("Hola")
    thinking = sdk.requests[0]["config"].thinking_config
    assert (thinking.thinking_budget, thinking.thinking_level) == (budget, level)
    sdk = _Sdk(_response(types.Part(text="Hola.")))
    _start(sdk, "gemini-2.0-flash").send_user("Hola")
    assert sdk.requests[0]["config"].thinking_config is None


def test_a_tool_round_keeps_the_history_intact():
    first = _response(types.Part(text="Un momento."), _call("get_pets", "id-1"),
                      _call("list_appointments"))
    sdk = _Sdk(first, _response(types.Part(text="Listo.")))
    conversation = _start(sdk)
    reply = conversation.send_user("¿Qué citas tengo?")
    assert reply.stop == "tool_calls" and reply.text == "Un momento."
    assert [(c.id, c.name) for c in reply.tool_calls] == [
        ("id-1", "get_pets"), ("list_appointments-2", "list_appointments"),
    ]
    conversation.send_tool_results([
        ToolResult("id-1", '{"pets": []}'),
        ToolResult("list_appointments-2", '{"error": "no"}', True),
    ])
    contents = sdk.requests[1]["contents"]
    assert [c.role for c in contents] == ["user", "model", "user"]
    said = first.candidates[0].content.parts  # the model's turn, part by part, untouched
    assert all(kept is part for kept, part in zip(contents[1].parts, said, strict=True))
    answers = [part.function_response for part in contents[2].parts]
    assert [(a.id, a.name, a.response) for a in answers] == [
        ("id-1", "get_pets", {"pets": []}),
        (None, "list_appointments", {"error": "no"}),
    ]


def test_text_is_handed_over_as_it_arrives():
    chunks = [
        _response(types.Part(text="Los sába"), finish=None),
        _response(types.Part(text="dos abrimos "), finish=None),
        _response(types.Part(text="a las diez."), _call("get_pets", "id-1")),
    ]
    sdk = _Sdk(chunks, _response(types.Part(text="Listo.")))
    conversation, heard = _start(sdk), []
    reply = conversation.send_user("¿Y los sábados?", heard.append)
    assert heard == ["Los sába", "dos abrimos ", "a las diez."]
    assert reply.text == "Los sábados abrimos a las diez." and reply.stop == "tool_calls"
    assert reply.usage.output_tokens == 45  # the last chunk's count, not the sum
    conversation.send_tool_results([ToolResult("id-1", "{}")])
    model_turn = sdk.requests[1]["contents"][1]
    assert model_turn.role == "model" and len(model_turn.parts) == 4


def test_thoughts_are_not_spoken():
    sdk = _Sdk(_response(types.Part(text="pensando...", thought=True), types.Part(text="Hola.")))
    assert _start(sdk).send_user("Hola").text == "Hola."


def test_blocked_and_cut_off_replies():
    blocked = types.GenerateContentResponse(candidates=[])
    assert _start(_Sdk(blocked)).send_user("x").stop == "refusal"
    unsafe = _response(finish=types.FinishReason.SAFETY)
    assert _start(_Sdk(unsafe)).send_user("x").stop == "refusal"
    cut = _response(types.Part(text="Le cuento que"), finish=types.FinishReason.MAX_TOKENS)
    assert _start(_Sdk(cut)).send_user("x").stop == "max_tokens"


def _refusal(message="Thinking level MINIMAL is not supported for this model."):
    return errors.APIError(400, {"error": {"message": message, "status": "INVALID_ARGUMENT"}})


def test_a_refused_thinking_setting_is_stepped_down_and_remembered():
    sdk = _Sdk(_refusal(), _response(types.Part(text="Hola.")),
               _response(types.Part(text="Hola.")))
    client = GeminiClient("gemini-flash-latest", client=sdk)
    assert client.start("SYSTEM", "CONTEXT", SPECS).send_user("Hola").text == "Hola."
    asked = [r["config"].thinking_config for r in sdk.requests]
    assert asked[0].thinking_level == types.ThinkingLevel.MINIMAL
    assert (asked[1].thinking_budget, asked[1].thinking_level) == (0, None)
    assert len(sdk.requests[1]["contents"]) == 1  # the same turn, asked again
    client.start("SYSTEM", "CONTEXT", SPECS).send_user("Hola")
    assert sdk.requests[2]["config"].thinking_config.thinking_budget == 0  # no second refusal


def test_a_400_that_no_thinking_setting_fixes_is_reported_as_it_came():
    sdk = _Sdk(_refusal("boom"), _refusal("later"), _refusal("later"), _refusal("later"),
               _response(types.Part(text="Hola.")))
    conversation = _start(sdk)
    with pytest.raises(LLMError) as caught:
        conversation.send_user("Hola")
    assert not caught.value.retryable and "boom" in str(caught.value)
    conversation.send_user("Hola")  # the failed turn left no trace, and nothing was unlearnt
    assert len(sdk.requests[4]["contents"]) == 1
    assert sdk.requests[4]["config"].thinking_config.thinking_level == types.ThinkingLevel.MINIMAL


@pytest.mark.parametrize("code,retryable", [(503, True), (429, True), (404, False)])
def test_provider_errors_become_llm_errors(code, retryable):
    failure = errors.APIError(code, {"error": {"message": "boom", "status": "X"}})
    sdk = _Sdk(failure, _response(types.Part(text="Hola.")))
    conversation = _start(sdk)
    with pytest.raises(LLMError) as caught:
        conversation.send_user("Hola")
    assert caught.value.retryable is retryable and "boom" in str(caught.value)
    conversation.send_user("Hola")  # the failed turn left no trace
    assert len(sdk.requests[1]["contents"]) == 1


def test_a_dropped_connection_is_retryable():
    with pytest.raises(LLMError) as caught:
        _start(_Sdk(httpx.ConnectError("down"))).send_user("Hola")
    assert caught.value.retryable


def test_a_missing_key_is_explained(monkeypatch):
    for variable in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GENAI_USE_VERTEXAI"):
        monkeypatch.delenv(variable, raising=False)
    with pytest.raises(LLMError, match="GEMINI_API_KEY"):
        create_client("gemini")


def test_the_model_id_picks_the_provider(monkeypatch):
    assert provider_of("gemini-2.5-flash") == "gemini"
    assert provider_of("claude-sonnet-5-5") == "anthropic"
    assert provider_of("something-else") is None and provider_of(None) is None
    monkeypatch.setenv("GEMINI_API_KEY", "not-a-real-key")
    assert isinstance(create_client(model="gemini-2.5-flash"), GeminiClient)


def test_with_nothing_said_the_agent_runs_on_the_model_that_was_measured(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "not-a-real-key")
    for variable in ("VETDESK_LLM_PROVIDER", "VETDESK_LLM_MODEL"):
        monkeypatch.delenv(variable, raising=False)
    client = create_client()
    assert isinstance(client, GeminiClient) and client.model == "gemini-3.5-flash-lite"
