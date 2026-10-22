"""The ONLY module that talks to an LLM provider.

Everything else calls ``LLMClient.complete_json(...)`` / ``complete_text(...)``
and never imports a provider SDK. Supported providers:

* ``foundry``       - Claude on Microsoft Foundry (``LLM_BASE_URL`` = https://<resource>.services.ai.azure.com/anthropic/
                      or ``LLM_FOUNDRY_RESOURCE`` = resource name; ``LLM_API_KEY`` = the Foundry key)
* ``azure_openai``  - OpenAI-family models on Azure OpenAI / Foundry (``LLM_BASE_URL`` = resource endpoint,
                      ``LLM_MODEL`` = deployment name, ``LLM_API_VERSION``)
* ``anthropic``     - Anthropic Claude first-party API
* ``openai``        - OpenAI, or any OpenAI-compatible endpoint via ``LLM_BASE_URL``
* ``mock``          - no LLM; the app falls back to deterministic heuristics

The provider is chosen by ``LLM_PROVIDER`` (``auto`` guesses from the key/URL).
Prompts and responses are never logged in full unless ``LLM_LOG_PROMPTS=true``.
"""

from __future__ import annotations

import json
import logging
import random
import time
from dataclasses import dataclass
from typing import Any

from .errors import LLMUnavailableError, UserFacingError
from .settings import Settings

log = logging.getLogger("dashboard.llm")


class LLMError(UserFacingError):
    """A friendly, user-presentable error from the model provider."""

    status_code = 502

    def __init__(self, message: str, transient: bool = False):
        super().__init__(message)
        self.transient = transient


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0

    def __add__(self, other: Usage) -> Usage:
        return Usage(self.input_tokens + other.input_tokens, self.output_tokens + other.output_tokens)


@dataclass
class LLMResponse:
    text: str
    data: dict | None
    usage: Usage
    elapsed_ms: float
    model: str


# --------------------------------------------------------------------------- #
# JSON helpers
# --------------------------------------------------------------------------- #
def parse_json_object(text: str) -> dict:
    """Parse a JSON object from model output, tolerating code fences and surrounding prose."""
    s = (text or "").strip()
    if s.startswith("```"):
        s = s.strip("`")
        if s.lower().startswith("json"):
            s = s[4:]
    try:
        data = json.loads(s)
    except json.JSONDecodeError:
        start, end = s.find("{"), s.rfind("}")
        if start == -1 or end <= start:
            raise LLMError("The model returned a response that wasn't valid JSON.") from None
        try:
            data = json.loads(s[start:end + 1])
        except json.JSONDecodeError as exc:
            raise LLMError("The model returned a response that wasn't valid JSON.") from exc
    if not isinstance(data, dict):
        raise LLMError("The model returned JSON in an unexpected shape (expected an object).")
    return data


# --------------------------------------------------------------------------- #
# Providers
# --------------------------------------------------------------------------- #
class BaseProvider:
    name = "base"

    def __init__(self, settings: Settings):
        self.settings = settings
        self.model = settings.effective_llm_model
        self.timeout = settings.llm_timeout_seconds
        self.max_retries = settings.llm_max_retries

    def complete(self, *, system: str, messages: list[dict], schema: dict | None, schema_name: str,
                 max_tokens: int) -> LLMResponse:
        raise NotImplementedError


class AnthropicProvider(BaseProvider):
    """Claude, either first-party (``anthropic``) or hosted on Microsoft Foundry (``foundry``)."""

    name = "anthropic"

    def __init__(self, settings: Settings, foundry: bool = False):
        super().__init__(settings)
        import anthropic

        self._sdk = anthropic
        kwargs: dict[str, Any] = {"api_key": settings.llm_api_key, "timeout": self.timeout,
                                  "max_retries": self.max_retries}
        if foundry:
            self.name = "foundry"
            if settings.llm_base_url:
                kwargs["base_url"] = settings.llm_base_url
            elif settings.llm_foundry_resource:
                kwargs["resource"] = settings.llm_foundry_resource
            else:
                raise LLMUnavailableError("foundry needs LLM_BASE_URL (https://<resource>.services.ai.azure.com/anthropic/) "
                                          "or LLM_FOUNDRY_RESOURCE.")
            self.client = anthropic.AnthropicFoundry(**kwargs)
        else:
            if settings.llm_base_url:
                kwargs["base_url"] = settings.llm_base_url
            self.client = anthropic.Anthropic(**kwargs)
        self.effort = settings.llm_effort if settings.llm_effort in {"low", "medium", "high", "xhigh", "max"} else ""
        # Server-side refusal fallbacks are a first-party beta; not available on Foundry.
        self.use_fallbacks = bool(settings.llm_anthropic_fallbacks) and not foundry

    def complete(self, *, system, messages, schema, schema_name, max_tokens) -> LLMResponse:
        start = time.perf_counter()
        output_config: dict[str, Any] = {}
        if schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": schema}
        if self.effort:
            output_config["effort"] = self.effort
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": messages,
        }
        if output_config:
            kwargs["output_config"] = output_config
        try:
            if self.use_fallbacks:
                kwargs["betas"] = ["server-side-fallback-2026-07-01"]
                kwargs["fallbacks"] = "default"
                with self.client.beta.messages.stream(**kwargs) as stream:
                    resp = stream.get_final_message()
            else:
                with self.client.messages.stream(**kwargs) as stream:
                    resp = stream.get_final_message()
        except Exception as exc:  # noqa: BLE001 - mapped to friendly errors below
            raise self._map_error(exc) from exc
        if resp.stop_reason == "refusal":
            raise LLMError("The model declined to process this request.")
        if resp.stop_reason == "max_tokens":
            raise LLMError("The model ran out of output tokens before finishing. Try a smaller request.")
        text = "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", "") == "text")
        usage = Usage(getattr(resp.usage, "input_tokens", 0) or 0, getattr(resp.usage, "output_tokens", 0) or 0)
        data = parse_json_object(text) if schema is not None else None
        return LLMResponse(text, data, usage, (time.perf_counter() - start) * 1000, getattr(resp, "model", self.model))

    def _map_error(self, exc: Exception) -> LLMError:
        a = self._sdk
        if isinstance(exc, a.AuthenticationError):
            return LLMError("The LLM API key was rejected. Check LLM_API_KEY.")
        if isinstance(exc, a.PermissionDeniedError):
            return LLMError("This API key doesn't have access to the configured model.")
        if isinstance(exc, a.NotFoundError):
            return LLMError(f"The configured model '{self.model}' was not found.")
        if isinstance(exc, a.RateLimitError):
            return LLMError("The AI provider is rate-limiting requests. Please retry in a moment.", transient=True)
        if isinstance(exc, a.APITimeoutError):
            return LLMError("The AI provider took too long to respond. Please retry.", transient=True)
        if isinstance(exc, a.APIConnectionError):
            return LLMError("Couldn't reach the AI provider. Check network/egress configuration.", transient=True)
        if isinstance(exc, a.BadRequestError):
            return LLMError(f"The AI provider rejected the request: {_api_message(exc)}")
        if isinstance(exc, a.APIStatusError):
            transient = getattr(exc, "status_code", 500) >= 500
            return LLMError(f"The AI provider returned an error ({exc.status_code}).", transient=transient)
        return LLMError(f"Unexpected error from the AI provider: {str(exc)[:300]}")


class OpenAIProvider(BaseProvider):
    """OpenAI, Azure OpenAI, and OpenAI-compatible gateways."""

    name = "openai"

    def __init__(self, settings: Settings, azure: bool = False):
        super().__init__(settings)
        import openai

        self._sdk = openai
        self.name = "azure_openai" if azure else "openai"
        if azure:
            if not settings.llm_base_url:
                raise LLMUnavailableError("azure_openai needs LLM_BASE_URL (the resource endpoint).")
            self.client = openai.AzureOpenAI(api_key=settings.llm_api_key, azure_endpoint=settings.llm_base_url,
                                             api_version=settings.llm_api_version, timeout=self.timeout,
                                             max_retries=self.max_retries)
        else:
            kwargs: dict[str, Any] = {"api_key": settings.llm_api_key, "timeout": self.timeout,
                                      "max_retries": self.max_retries}
            if settings.llm_base_url:
                kwargs["base_url"] = settings.llm_base_url
            self.client = openai.OpenAI(**kwargs)
        self._strict_supported = True

    def complete(self, *, system, messages, schema, schema_name, max_tokens) -> LLMResponse:
        start = time.perf_counter()
        chat = [{"role": "system", "content": system}, *messages]
        kwargs: dict[str, Any] = {"model": self.model, "messages": chat, "max_completion_tokens": max_tokens}
        if schema is not None:
            if self._strict_supported:
                kwargs["response_format"] = {"type": "json_schema",
                                             "json_schema": {"name": schema_name, "schema": schema, "strict": True}}
            else:
                kwargs["response_format"] = {"type": "json_object"}
        try:
            resp = self.client.chat.completions.create(**kwargs)
        except self._sdk.BadRequestError as exc:
            # Some gateways / older deployments don't support strict JSON schema: degrade to json_object once.
            if schema is not None and self._strict_supported and "json_schema" in str(exc).lower():
                log.warning("provider rejected json_schema response_format; falling back to json_object")
                self._strict_supported = False
                return self.complete(system=system, messages=messages, schema=schema, schema_name=schema_name,
                                     max_tokens=max_tokens)
            raise self._map_error(exc) from exc
        except Exception as exc:  # noqa: BLE001
            raise self._map_error(exc) from exc
        choice = resp.choices[0]
        if getattr(choice.message, "refusal", None):
            raise LLMError("The model declined to process this request.")
        if choice.finish_reason == "length":
            raise LLMError("The model ran out of output tokens before finishing. Try a smaller request.")
        text = choice.message.content or ""
        usage = Usage(resp.usage.prompt_tokens, resp.usage.completion_tokens) if resp.usage else Usage()
        data = parse_json_object(text) if schema is not None else None
        return LLMResponse(text, data, usage, (time.perf_counter() - start) * 1000, getattr(resp, "model", self.model))

    def _map_error(self, exc: Exception) -> LLMError:
        o = self._sdk
        if isinstance(exc, o.AuthenticationError):
            return LLMError("The LLM API key was rejected. Check LLM_API_KEY.")
        if isinstance(exc, o.PermissionDeniedError):
            return LLMError("This API key doesn't have access to the configured model/deployment.")
        if isinstance(exc, o.NotFoundError):
            return LLMError(f"The configured model/deployment '{self.model}' was not found.")
        if isinstance(exc, o.RateLimitError):
            return LLMError("The AI provider is rate-limiting requests. Please retry in a moment.", transient=True)
        if isinstance(exc, o.APITimeoutError):
            return LLMError("The AI provider took too long to respond. Please retry.", transient=True)
        if isinstance(exc, o.APIConnectionError):
            return LLMError("Couldn't reach the AI provider. Check network/egress configuration.", transient=True)
        if isinstance(exc, o.BadRequestError):
            return LLMError(f"The AI provider rejected the request: {_api_message(exc)}")
        if isinstance(exc, o.APIStatusError):
            transient = getattr(exc, "status_code", 500) >= 500
            return LLMError(f"The AI provider returned an error ({exc.status_code}).", transient=transient)
        return LLMError(f"Unexpected error from the AI provider: {str(exc)[:300]}")


def _api_message(exc: Exception) -> str:
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        err = body.get("error", body)
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])[:400]
    return str(exc)[:400]


# --------------------------------------------------------------------------- #
# Facade
# --------------------------------------------------------------------------- #
class LLMClient:
    """Provider-agnostic facade with outer retry/backoff and usage logging."""

    def __init__(self, provider: BaseProvider, settings: Settings):
        self.provider = provider
        self.settings = settings
        self.total_usage = Usage()

    @property
    def provider_name(self) -> str:
        return self.provider.name

    @property
    def model(self) -> str:
        return self.provider.model

    def complete_json(self, *, system: str, user: str | list[dict], schema: dict, schema_name: str = "response",
                      max_tokens: int = 16_000) -> LLMResponse:
        return self._call(system=system, user=user, schema=schema, schema_name=schema_name, max_tokens=max_tokens)

    def complete_text(self, *, system: str, user: str | list[dict], max_tokens: int = 4_000) -> LLMResponse:
        return self._call(system=system, user=user, schema=None, schema_name="text", max_tokens=max_tokens)

    def _call(self, *, system, user, schema, schema_name, max_tokens) -> LLMResponse:
        messages = user if isinstance(user, list) else [{"role": "user", "content": user}]
        if self.settings.llm_log_prompts:
            log.debug("LLM request [%s]\nSYSTEM:\n%s\nMESSAGES:\n%s", schema_name, system,
                      json.dumps(messages, ensure_ascii=False)[:20_000])
        attempts = 3
        for attempt in range(1, attempts + 1):
            try:
                resp = self.provider.complete(system=system, messages=messages, schema=schema,
                                              schema_name=schema_name, max_tokens=max_tokens)
            except LLMError as exc:
                if exc.transient and attempt < attempts:
                    delay = min(2 ** attempt + random.random(), 20)
                    log.warning("LLM transient error (attempt %d/%d): %s - retrying in %.1fs", attempt, attempts,
                                exc, delay)
                    time.sleep(delay)
                    continue
                raise
            self.total_usage = self.total_usage + resp.usage
            log.info("LLM call %s provider=%s model=%s in=%d out=%d latency=%.0fms", schema_name,
                     self.provider_name, resp.model, resp.usage.input_tokens, resp.usage.output_tokens,
                     resp.elapsed_ms)
            if self.settings.llm_log_prompts:
                log.debug("LLM response [%s]:\n%s", schema_name, resp.text[:20_000])
            return resp
        raise LLMError("The AI provider is unavailable right now.", transient=True)  # pragma: no cover


def build_llm_client(settings: Settings) -> LLMClient | None:
    """Return a client, or ``None`` when no LLM is configured (the app then uses heuristics only)."""
    provider = settings.effective_llm_provider
    if provider == "mock":
        return None
    if not settings.llm_api_key:
        raise LLMUnavailableError(f"LLM_PROVIDER={provider} but LLM_API_KEY is empty.")
    if provider == "anthropic":
        return LLMClient(AnthropicProvider(settings), settings)
    if provider == "foundry":
        return LLMClient(AnthropicProvider(settings, foundry=True), settings)
    if provider == "openai":
        return LLMClient(OpenAIProvider(settings), settings)
    if provider == "azure_openai":
        return LLMClient(OpenAIProvider(settings, azure=True), settings)
    raise LLMUnavailableError(f"Unknown LLM_PROVIDER: {settings.llm_provider!r}")
