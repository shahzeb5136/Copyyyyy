"""Call the LLM for a structured object, validate with Pydantic, and retry with the errors fed back."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from ..llm_client import LLMClient, LLMError, LLMResponse
from .prompts import repair_prompt
from .schema_tools import strict_schema_copy

T = TypeVar("T", bound=BaseModel)


@dataclass
class StructuredResult:
    obj: BaseModel | None
    response: LLMResponse | None
    errors: list[str] = field(default_factory=list)
    attempts: int = 0


def format_validation_error(exc: ValidationError) -> list[str]:
    out = []
    for e in exc.errors()[:15]:
        loc = ".".join(str(p) for p in e.get("loc", ()))
        out.append(f"{loc}: {e.get('msg', '')}")
    return out


def call_structured(llm: LLMClient, *, system: str, user: str, model_cls: type[T], schema_name: str,
                    max_tokens: int = 16_000, max_attempts: int = 3,
                    validate_extra: Callable[[T], list[str]] | None = None) -> StructuredResult:
    """Returns the last parsed object (possibly with remaining ``errors``) so callers can decide what to do."""
    messages: list[dict] = [{"role": "user", "content": user}]
    schema = strict_schema_copy(model_cls)
    last_obj: BaseModel | None = None
    last_resp: LLMResponse | None = None
    errors: list[str] = []
    attempt = 0
    while attempt < max_attempts:
        attempt += 1
        try:
            resp = llm.complete_json(system=system, user=messages, schema=schema, schema_name=schema_name,
                                     max_tokens=max_tokens)
        except LLMError as exc:
            if "wasn't valid JSON" in str(exc) and attempt < max_attempts:
                messages.append({"role": "user", "content": "That was not valid JSON. Return only the JSON object."})
                continue
            raise
        last_resp = resp
        try:
            obj = model_cls.model_validate(resp.data)
        except ValidationError as exc:
            errors = format_validation_error(exc)
            obj = None
        else:
            last_obj = obj
            errors = validate_extra(obj) if validate_extra else []
        if not errors:
            return StructuredResult(obj, resp, [], attempt)
        if attempt < max_attempts:
            messages.append({"role": "assistant", "content": resp.text})
            messages.append({"role": "user", "content": repair_prompt(errors)})
    if last_obj is None:
        raise LLMError("The AI could not produce a valid result: " + "; ".join(errors[:3]))
    return StructuredResult(last_obj, last_resp, errors, attempt)
