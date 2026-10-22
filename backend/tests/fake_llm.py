"""A scripted stand-in for the LLM provider, for tests."""

from __future__ import annotations

import json
from collections.abc import Callable

from app.llm_client import BaseProvider, LLMClient, LLMResponse, Usage
from app.settings import Settings


class FakeProvider(BaseProvider):
    name = "fake"

    def __init__(self, answers: list[str | dict | Callable]):
        self.answers = list(answers)
        self.model = "fake-model"
        self.calls: list[dict] = []
        self.timeout = 1
        self.max_retries = 0

    def complete(self, *, system, messages, schema, schema_name, max_tokens) -> LLMResponse:
        self.calls.append({"system": system, "messages": messages, "schema_name": schema_name})
        if not self.answers:
            raise AssertionError(f"FakeProvider has no answer left for {schema_name}")
        answer = self.answers.pop(0)
        if callable(answer):
            answer = answer(messages)
        text = json.dumps(answer) if isinstance(answer, dict) else str(answer)
        data = None
        if schema is not None:
            from app.llm_client import parse_json_object

            data = parse_json_object(text)
        return LLMResponse(text=text, data=data, usage=Usage(10, 5), elapsed_ms=1.0, model=self.model)


def fake_llm(answers: list) -> LLMClient:
    return LLMClient(FakeProvider(answers), Settings(llm_provider="mock", _env_file=None))
