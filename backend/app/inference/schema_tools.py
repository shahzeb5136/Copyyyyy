"""Generate *strict* JSON Schemas for LLM structured output from the Pydantic spec models.

Provider strict modes (Anthropic ``output_config.format``, OpenAI ``strict: true``)
want every property required, ``additionalProperties: false``, no defaults and no
``$ref``. Pydantic's schema has defaults and ``$defs``; this module rewrites it.
"""

from __future__ import annotations

import copy
from functools import lru_cache
from typing import Any

from pydantic import BaseModel

_KEEP = {"type", "properties", "required", "additionalProperties", "items", "enum", "description", "anyOf"}


def _inline_refs(node: Any, defs: dict[str, Any]) -> Any:
    if isinstance(node, dict):
        if "$ref" in node:
            ref = node["$ref"].rsplit("/", 1)[-1]
            target = copy.deepcopy(defs[ref])
            # Keep a description from the referencing site if present.
            if "description" in node and "description" not in target:
                target["description"] = node["description"]
            return _inline_refs(target, defs)
        return {k: _inline_refs(v, defs) for k, v in node.items()}
    if isinstance(node, list):
        return [_inline_refs(v, defs) for v in node]
    return node


def _strictify(node: Any) -> Any:
    if isinstance(node, dict):
        out: dict[str, Any] = {}
        for k, v in node.items():
            if k not in _KEEP:
                continue
            if k == "properties" and isinstance(v, dict):
                out[k] = {name: _strictify(sub) for name, sub in v.items()}  # keys are property names, keep all
            else:
                out[k] = _strictify(v)
        if out.get("type") == "object" and "properties" in out:
            out["required"] = list(out["properties"].keys())
            out["additionalProperties"] = False
        # Nullable unions (anyOf [X, null]) are not used by the LLM-facing models; collapse if they appear.
        if "anyOf" in out:
            non_null = [o for o in out["anyOf"] if o.get("type") != "null"]
            if len(non_null) == 1:
                merged = non_null[0]
                merged.setdefault("description", out.get("description", ""))
                return merged
        return out
    if isinstance(node, list):
        return [_strictify(v) for v in node]
    return node


@lru_cache(maxsize=32)
def strict_schema(model: type[BaseModel]) -> dict[str, Any]:
    raw = model.model_json_schema(by_alias=True)
    defs = raw.pop("$defs", {})
    inlined = _inline_refs(raw, defs)
    schema = _strictify(inlined)
    schema.pop("description", None)  # top-level docstrings aren't needed
    return schema


def strict_schema_copy(model: type[BaseModel]) -> dict[str, Any]:
    return copy.deepcopy(strict_schema(model))
