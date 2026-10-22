"""Spec replay is deterministic: same file + same spec -> same output."""

from __future__ import annotations

import json

from app.engine.dashboard import build_dashboard
from app.inference.dashboard import design_dashboard
from app.inference.heuristics import heuristic_semantic_model
from app.spec.migrations import load_spec


def _strip(result: dict) -> dict:
    result.pop("built_at", None)
    return result


def test_same_input_same_output(budget_table, budget_profile):
    model = heuristic_semantic_model(budget_profile)
    spec, _ = design_dashboard(budget_table.df, budget_profile, model, None, "1.0.0")
    saved = json.dumps(spec.to_json_dict())
    a = _strip(build_dashboard(budget_table.df, load_spec(json.loads(saved))).model_dump(mode="json"))
    b = _strip(build_dashboard(budget_table.df, load_spec(json.loads(saved))).model_dump(mode="json"))
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    assert all(w["status"] in ("ok", "pending") for w in a["widgets"])
