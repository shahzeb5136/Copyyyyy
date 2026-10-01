"""Thin, provider-agnostic wrapper around the LLM APIs.

Two operations are all the agents need:

* ``generate_json`` - get a JSON object that conforms to a JSON Schema
  (Anthropic structured outputs / OpenAI strict JSON schema).
* ``stream_text``   - stream a free-text answer token by token.

Each call records token usage so the UI can show what every agent cost.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from dataclasses import dataclass


class LLMError(Exception):
    """A friendly, user-presentable error from the model provider."""


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0

    def __add__(self, other: Usage) -> Usage:
        return Usage(self.input_tokens + other.input_tokens, self.output_tokens + other.output_tokens)


@dataclass
class JSONResult:
    data: dict
    usage: Usage
    elapsed_ms: float


class BaseLLM:
    provider: str = ""

    def __init__(self, api_key: str, model: str, timeout: float = 90.0):
        if not api_key:
            raise LLMError("No API key configured.")
        self.model = model
        self.timeout = timeout
        self.last_stream_usage = Usage()

    def generate_json(self, *, system: str, user: str, schema: dict, schema_name: str,
                      max_tokens: int = 8_000) -> JSONResult:
        raise NotImplementedError

    def stream_text(self, *, system: str, user: str, max_tokens: int = 4_000) -> Iterator[str]:
        raise NotImplementedError


# --------------------------------------------------------------------------- #
# Anthropic
# --------------------------------------------------------------------------- #
class AnthropicLLM(BaseLLM):
    provider = "anthropic"

    def __init__(self, api_key: str, model: str, effort: str = "", timeout: float = 90.0, http_client=None):
        super().__init__(api_key, model, timeout)
        import anthropic

        self._anthropic = anthropic
        self.client = anthropic.Anthropic(api_key=api_key, timeout=timeout, max_retries=2, http_client=http_client)
        self.effort = effort if effort in {"low", "medium", "high"} else ""

    def _output_config(self, schema: dict | None = None) -> dict | None:
        cfg: dict = {}
        if schema is not None:
            cfg["format"] = {"type": "json_schema", "schema": schema}
        if self.effort:
            cfg["effort"] = self.effort
        return cfg or None

    def generate_json(self, *, system, user, schema, schema_name, max_tokens=8_000) -> JSONResult:
        start = time.perf_counter()
        kwargs = dict(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        cfg = self._output_config(schema)
        if cfg:
            kwargs["output_config"] = cfg
        try:
            resp = self.client.messages.create(**kwargs)
        except Exception as exc:  # noqa: BLE001 - mapped to friendly errors
            raise _map_anthropic_error(self._anthropic, exc) from exc
        if resp.stop_reason == "refusal":
            raise LLMError("The model declined to answer this request.")
        if resp.stop_reason == "max_tokens":
            raise LLMError("The model ran out of output tokens before finishing. Try a simpler question.")
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        data = _parse_json(text)
        usage = Usage(resp.usage.input_tokens, resp.usage.output_tokens)
        return JSONResult(data, usage, (time.perf_counter() - start) * 1000)

    def stream_text(self, *, system, user, max_tokens=4_000) -> Iterator[str]:
        self.last_stream_usage = Usage()
        kwargs = dict(model=self.model, max_tokens=max_tokens, system=system,
                      messages=[{"role": "user", "content": user}])
        cfg = self._output_config()
        if cfg:
            kwargs["output_config"] = cfg
        try:
            with self.client.messages.stream(**kwargs) as stream:
                yield from stream.text_stream
                final = stream.get_final_message()
                self.last_stream_usage = Usage(final.usage.input_tokens, final.usage.output_tokens)
        except LLMError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise _map_anthropic_error(self._anthropic, exc) from exc


def _map_anthropic_error(anthropic, exc: Exception) -> LLMError:
    if isinstance(exc, anthropic.AuthenticationError):
        return LLMError("The Anthropic API key was rejected. Check that it is correct and active.")
    if isinstance(exc, anthropic.PermissionDeniedError):
        return LLMError("This API key doesn't have access to the selected model.")
    if isinstance(exc, anthropic.NotFoundError):
        return LLMError("The selected model was not found. Pick another model in the sidebar.")
    if isinstance(exc, anthropic.RateLimitError):
        return LLMError("The AI provider is rate-limiting requests right now. Please wait a moment and retry.")
    if isinstance(exc, (getattr(anthropic, "OverloadedError", ()), anthropic.InternalServerError)):
        return LLMError("The AI provider is temporarily overloaded. Please try again in a moment.")
    if isinstance(exc, anthropic.APITimeoutError):
        return LLMError("The AI provider took too long to respond. Please try again.")
    if isinstance(exc, anthropic.APIConnectionError):
        return LLMError("Couldn't reach the AI provider. Check the network connection and try again.")
    if isinstance(exc, anthropic.BadRequestError):
        return LLMError(f"The AI provider rejected the request: {_api_message(exc)}")
    if isinstance(exc, anthropic.APIStatusError):
        return LLMError(f"The AI provider returned an error ({exc.status_code}). Please try again.")
    return LLMError(f"Unexpected error from the AI provider: {exc}")


# --------------------------------------------------------------------------- #
# OpenAI
# --------------------------------------------------------------------------- #
class OpenAILLM(BaseLLM):
    provider = "openai"

    def __init__(self, api_key: str, model: str, timeout: float = 90.0, http_client=None):
        super().__init__(api_key, model, timeout)
        import openai

        self._openai = openai
        self.client = openai.OpenAI(api_key=api_key, timeout=timeout, max_retries=2, http_client=http_client)

    def generate_json(self, *, system, user, schema, schema_name, max_tokens=8_000) -> JSONResult:
        start = time.perf_counter()
        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": schema_name, "schema": schema, "strict": True},
                },
                max_completion_tokens=max_tokens,
            )
        except Exception as exc:  # noqa: BLE001
            raise _map_openai_error(self._openai, exc) from exc
        choice = resp.choices[0]
        if getattr(choice.message, "refusal", None):
            raise LLMError("The model declined to answer this request.")
        if choice.finish_reason == "length":
            raise LLMError("The model ran out of output tokens before finishing. Try a simpler question.")
        data = _parse_json(choice.message.content or "")
        usage = Usage(resp.usage.prompt_tokens, resp.usage.completion_tokens) if resp.usage else Usage()
        return JSONResult(data, usage, (time.perf_counter() - start) * 1000)

    def stream_text(self, *, system, user, max_tokens=4_000) -> Iterator[str]:
        self.last_stream_usage = Usage()
        try:
            stream = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                max_completion_tokens=max_tokens,
                stream=True,
                stream_options={"include_usage": True},
            )
            for chunk in stream:
                if chunk.usage:
                    self.last_stream_usage = Usage(chunk.usage.prompt_tokens, chunk.usage.completion_tokens)
                if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
        except Exception as exc:  # noqa: BLE001
            raise _map_openai_error(self._openai, exc) from exc


def _map_openai_error(openai, exc: Exception) -> LLMError:
    if isinstance(exc, openai.AuthenticationError):
        return LLMError("The OpenAI API key was rejected. Check that it is correct and active.")
    if isinstance(exc, openai.PermissionDeniedError):
        return LLMError("This API key doesn't have access to the selected model.")
    if isinstance(exc, openai.NotFoundError):
        return LLMError("The selected model was not found. Pick another model in the sidebar.")
    if isinstance(exc, openai.RateLimitError):
        return LLMError("The AI provider is rate-limiting requests (or the account is out of credit). Retry shortly.")
    if isinstance(exc, openai.APITimeoutError):
        return LLMError("The AI provider took too long to respond. Please try again.")
    if isinstance(exc, openai.APIConnectionError):
        return LLMError("Couldn't reach the AI provider. Check the network connection and try again.")
    if isinstance(exc, openai.BadRequestError):
        return LLMError(f"The AI provider rejected the request: {_api_message(exc)}")
    if isinstance(exc, openai.APIStatusError):
        return LLMError(f"The AI provider returned an error ({exc.status_code}). Please try again.")
    return LLMError(f"Unexpected error from the AI provider: {exc}")


# --------------------------------------------------------------------------- #
def _api_message(exc: Exception) -> str:
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        err = body.get("error", body)
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])[:400]
    return str(exc)[:400]


def _parse_json(text: str) -> dict:
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{"):]
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Last resort: grab the outermost object.
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise LLMError("The model returned a response that wasn't valid JSON.") from None
        try:
            data = json.loads(text[start:end + 1])
        except json.JSONDecodeError as exc:
            raise LLMError("The model returned a response that wasn't valid JSON.") from exc
    if not isinstance(data, dict):
        raise LLMError("The model returned JSON in an unexpected shape.")
    return data


def make_llm(provider: str, api_key: str, model: str, effort: str = "") -> BaseLLM:
    if provider == "anthropic":
        return AnthropicLLM(api_key, model, effort=effort)
    if provider == "openai":
        return OpenAILLM(api_key, model)
    if provider == "offline":
        from .offline_llm import OfflineLLM

        return OfflineLLM()
    raise LLMError(f"Unknown provider: {provider}")
