"""Provider adapters, tested against mocked HTTP so no API key or network is needed."""

import json

import httpx2 as httpx  # the Anthropic and OpenAI SDKs are built on httpx2
import pytest

from core import prompts
from core.llm import AnthropicLLM, LLMError, OpenAILLM


def _anthropic_message(text, stop_reason="end_turn"):
    return {
        "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-sonnet-5-5",
        "content": [{"type": "text", "text": text}], "stop_reason": stop_reason, "stop_sequence": None,
        "usage": {"input_tokens": 120, "output_tokens": 30},
    }


def _anthropic_sse(chunks):
    events = [("message_start", {"type": "message_start", "message": {**_anthropic_message(""), "content": [],
                                                                         "stop_reason": None}}),
              ("content_block_start", {"type": "content_block_start", "index": 0,
                                       "content_block": {"type": "text", "text": ""}})]
    for c in chunks:
        events.append(("content_block_delta", {"type": "content_block_delta", "index": 0,
                                               "delta": {"type": "text_delta", "text": c}}))
    events += [("content_block_stop", {"type": "content_block_stop", "index": 0}),
               ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                                  "usage": {"output_tokens": 9}}),
               ("message_stop", {"type": "message_stop"})]
    return "".join(f"event: {e}\ndata: {json.dumps(d)}\n\n" for e, d in events)


def test_anthropic_structured_output_request_and_parse():
    seen = {}

    def handler(request: httpx.Request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=_anthropic_message('{"reasoning":"r","can_answer":true,"sql":"SELECT 1",'
                                                            '"assumptions":""}'))

    llm = AnthropicLLM("sk-test", "claude-sonnet-5-5", http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    res = llm.generate_json(system="s", user="u", schema=prompts.SQL_SCHEMA, schema_name="sql_query")
    assert res.data["sql"] == "SELECT 1"
    assert res.usage.input_tokens == 120
    body = seen["body"]
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert body["output_config"]["format"]["schema"]["required"] == ["reasoning", "can_answer", "sql", "assumptions"]
    assert "tool_choice" not in body and body["system"] == "s"


def test_anthropic_effort_is_passed_when_configured():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=_anthropic_message('{"reasoning":"","charts":[]}'))

    llm = AnthropicLLM("sk", "claude-opus-5-5", effort="low",
                       http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    llm.generate_json(system="s", user="u", schema=prompts.VIZ_SCHEMA, schema_name="chart_plan")
    assert seen["body"]["output_config"]["effort"] == "low"


def test_anthropic_streaming():
    def handler(request):
        return httpx.Response(200, text=_anthropic_sse(["Revenue ", "grew **12%**."]),
                              headers={"content-type": "text/event-stream"})

    llm = AnthropicLLM("sk", "claude-sonnet-5-5", http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert "".join(llm.stream_text(system="s", user="u")) == "Revenue grew **12%**."
    assert llm.last_stream_usage.output_tokens == 9


@pytest.mark.parametrize("status, needle", [(401, "rejected"), (404, "not found"), (429, "rate"), (529, "overloaded")])
def test_anthropic_errors_are_friendly(status, needle):
    def handler(request):
        return httpx.Response(status, json={"type": "error", "error": {"type": "x", "message": "nope"}})

    llm = AnthropicLLM("sk", "m", http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    llm.client = llm.client.with_options(max_retries=0)
    with pytest.raises(LLMError) as exc:
        llm.generate_json(system="s", user="u", schema=prompts.SQL_SCHEMA, schema_name="sql_query")
    assert needle in str(exc.value).lower()


def test_anthropic_max_tokens_stop_reason():
    def handler(request):
        return httpx.Response(200, json=_anthropic_message('{"reas', stop_reason="max_tokens"))

    llm = AnthropicLLM("sk", "m", http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(LLMError):
        llm.generate_json(system="s", user="u", schema=prompts.SQL_SCHEMA, schema_name="sql_query")


def test_openai_structured_output_request_and_parse():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "id": "c1", "object": "chat.completion", "created": 1, "model": "gpt-5.4-mini",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": '{"reasoning":"","charts":[]}'}}],
            "usage": {"prompt_tokens": 50, "completion_tokens": 5, "total_tokens": 55},
        })

    llm = OpenAILLM("sk", "gpt-5.4-mini", http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    res = llm.generate_json(system="s", user="u", schema=prompts.VIZ_SCHEMA, schema_name="chart_plan")
    assert res.data == {"reasoning": "", "charts": []}
    fmt = seen["body"]["response_format"]
    assert fmt["type"] == "json_schema" and fmt["json_schema"]["strict"] is True
    assert seen["body"]["messages"][0]["role"] == "system"


def test_openai_streaming():
    chunks = [{"id": "c", "object": "chat.completion.chunk", "created": 1, "model": "m",
               "choices": [{"index": 0, "delta": {"content": t}, "finish_reason": None}]} for t in ["Hi ", "there"]]
    chunks.append({"id": "c", "object": "chat.completion.chunk", "created": 1, "model": "m", "choices": [],
                   "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}})
    body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"

    def handler(request):
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    llm = OpenAILLM("sk", "m", http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert "".join(llm.stream_text(system="s", user="u")) == "Hi there"
    assert llm.last_stream_usage.output_tokens == 2


def _all_objects_closed(schema):
    if schema.get("type") == "object":
        assert schema.get("additionalProperties") is False
        assert set(schema["required"]) == set(schema["properties"])
        for v in schema["properties"].values():
            _all_objects_closed(v)
    if schema.get("type") == "array":
        _all_objects_closed(schema["items"])


@pytest.mark.parametrize("schema", [prompts.SQL_SCHEMA, prompts.VIZ_SCHEMA, prompts.DICTIONARY_SCHEMA])
def test_schemas_are_strict_mode_compatible(schema):
    _all_objects_closed(schema)
