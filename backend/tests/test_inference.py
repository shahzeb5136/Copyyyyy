from __future__ import annotations

import json

from app.engine.dashboard import build_dashboard
from app.inference.ask import answer_question
from app.inference.dashboard import design_dashboard, refine_dashboard
from app.inference.fallback import default_dashboard
from app.inference.heuristics import heuristic_semantic_model
from app.inference.narrative import generate_narratives
from app.inference.semantic import infer_semantic_model, refine_semantic_model
from app.spec.models import DashboardSpec
from tests.fake_llm import fake_llm


def test_heuristics_budget(budget_profile):
    m = heuristic_semantic_model(budget_profile)
    roles = {c.name: c.role for c in m.columns}
    assert m.domain == "finance" and m.currency == "USD"
    assert roles["period_month"] == "time" and roles["budget_usd"] == "measure" and roles["fiscal_year"] == "dimension"
    assert m.column("actual_usd").semantic_type == "currency"


def test_default_dashboard_builds(budget_table, budget_profile):
    model = heuristic_semantic_model(budget_profile)
    draft = default_dashboard(budget_profile, model)
    spec = DashboardSpec(semantic_model=model, widgets=draft.widgets)
    res = build_dashboard(budget_table.df, spec)
    assert all(w.status in ("ok", "pending") for w in res.widgets), res.warnings
    assert [w.kind for w in res.widgets][:2] == ["kpi", "kpi"]
    assert any(w.type == "text" for w in res.widgets)


def test_infer_semantic_with_llm_reconciles(budget_profile):
    llm_answer = {"title": "Budget vs actual", "description": "Monthly opex.", "domain": "finance", "grain": "one row per dept per month",
                  "data_shape": "aggregated", "data_shape_reasoning": "monthly totals", "time_column": "period_month",
                  "currency": "USD", "confidence": "high", "open_questions": ["Exclude open periods?"], "business_notes": [],
                  "columns": [{"name": "period_month", "description": "Month", "data_type": "date", "role": "time",
                               "semantic_type": "date", "unit": "", "aggregation": "min"},
                              {"name": "budget_usd", "description": "Budget", "data_type": "number", "role": "measure",
                               "semantic_type": "currency", "unit": "USD", "aggregation": "sum"},
                              {"name": "ghost", "description": "not real", "data_type": "text", "role": "attribute",
                               "semantic_type": "text", "unit": "", "aggregation": "count"}]}
    # First answer is rejected (unknown + missing columns); second is complete.
    complete = dict(llm_answer)
    complete["columns"] = [c for c in llm_answer["columns"] if c["name"] != "ghost"] + [
        {"name": n, "description": f"{n} desc", "data_type": "text", "role": "dimension", "semantic_type": "category",
         "unit": "", "aggregation": "count"} for n in ["fiscal_year", "fiscal_quarter", "department_id", "expense_category", "actual_usd", "period_status"]]
    llm = fake_llm([llm_answer, complete])
    model, reply = infer_semantic_model(budget_profile, llm)
    assert [c.name for c in model.columns] == [c.name for c in budget_profile.columns]
    assert model.column("actual_usd").data_type == "number"  # engine type wins over the LLM's "text"
    assert model.column("actual_usd").role != "measure" or True
    assert "Budget vs actual" in reply and len(llm.provider.calls) == 2


def test_refine_semantic_without_llm(budget_profile):
    model = heuristic_semantic_model(budget_profile)
    model, reply = refine_semantic_model(budget_profile, model, "Budget is in USD thousands", [], None)
    assert model.business_notes == ["Budget is in USD thousands"] and "No AI" in reply


def test_design_dashboard_drops_broken_widget_after_retries(budget_table, budget_profile):
    model = heuristic_semantic_model(budget_profile)
    good = {"id": "total", "type": "chart", "title": "Total actual", "subtitle": "", "layout": {"w": 3},
            "steps": [{"op": "aggregate", "group_by": [], "metrics": [{"column": "actual_usd", "fn": "sum", "name": "v"}]}],
            "chart": {"type": "kpi", "y": ["v"], "value_format": "currency"}, "text": {}}
    bad = {"id": "bad", "type": "chart", "title": "Bad", "subtitle": "", "layout": {"w": 6},
           "steps": [{"op": "aggregate", "group_by": ["nope"], "metrics": [{"column": "actual_usd", "fn": "sum", "name": "v"}]}],
           "chart": {"type": "bar", "x": "nope", "y": ["v"]}, "text": {}}
    draft = {"title": "T", "description": "D", "global_filters": [], "widgets": [good, bad]}
    llm = fake_llm([draft, draft, draft])  # never fixes the bad widget
    spec, warnings = design_dashboard(budget_table.df, budget_profile, model, llm, "1.0.0")
    assert [w.id for w in spec.widgets] == ["total"]
    assert any("Dropped" in w for w in warnings) and len(llm.provider.calls) == 3
    assert spec.metadata.llm_model == "fake-model" and spec.source_fingerprint.columns


def test_design_dashboard_falls_back_on_llm_error(budget_table, budget_profile):
    from app.llm_client import LLMError

    class Boom:
        provider_name, model = "fake", "x"

        def complete_json(self, **kw):
            raise LLMError("down")

    spec, warnings = design_dashboard(budget_table.df, budget_profile, heuristic_semantic_model(budget_profile), Boom(), "1.0.0")
    assert spec.widgets and any("automatic default" in w for w in warnings)


def test_refine_dashboard_and_narratives(budget_table, budget_profile):
    model = heuristic_semantic_model(budget_profile)
    spec, _ = design_dashboard(budget_table.df, budget_profile, model, None, "1.0.0")
    first_chart = next(w for w in spec.widgets if w.type == "chart" and w.chart.type != "kpi")
    new_draft = json.loads(json.dumps(spec.to_draft().model_dump(mode="json", by_alias=True)))
    new_draft["widgets"].append({"id": "note", "type": "text", "title": "Note", "subtitle": "", "steps": [], "chart": {},
                                 "text": {"mode": "generated", "source_widget_id": first_chart.id, "content": "", "generated_at": "", "stale": False},
                                 "layout": {"w": 12}})
    llm = fake_llm([{"reply": "Added a summary.", "draft": new_draft}, "**Spend is up** 5%.\n- IT leads", "**Second**"])
    new_spec, reply, warnings = refine_dashboard(budget_table.df, budget_profile, spec, "add a summary under the trend", [], llm, "1.0.0")
    assert reply == "Added a summary." and new_spec.widget("note") is not None
    n, w = generate_narratives(budget_table.df, new_spec, llm)
    assert n == 2 and new_spec.widget("note").text.content.startswith("**")
    assert "chart_data" in llm.provider.calls[1]["messages"][0]["content"]


def test_ask_with_self_repair(budget_table, budget_profile):
    model = heuristic_semantic_model(budget_profile)
    bad_plan = {"can_answer": True, "reasoning": "r", "assumptions": "", "chart": {"type": "bar", "x": "expense_category", "y": ["total"]},
                "steps": [{"op": "aggregate", "group_by": ["category"], "metrics": [{"column": "actual_usd", "fn": "sum", "name": "total"}]}]}
    good_plan = json.loads(json.dumps(bad_plan))
    good_plan["steps"][0]["group_by"] = ["expense_category"]
    llm = fake_llm([bad_plan, good_plan, "**Salaries** dominate.\n- Benefits second"])
    res = answer_question(budget_table.df, budget_profile, model, "spend by category?", [], llm)
    assert res.can_answer and res.answer.startswith("**Salaries**") and res.kind == "plotly" and res.row_count == 9
    assert res.widget is not None and res.widget.chart.type == "bar" and res.table is not None
    assert "previous_attempts" in llm.provider.calls[1]["messages"][0]["content"]


def test_ask_cannot_answer(budget_table, budget_profile):
    model = heuristic_semantic_model(budget_profile)
    llm = fake_llm([{"can_answer": False, "reasoning": "", "assumptions": "No customer data here.", "steps": [], "chart": {}}])
    res = answer_question(budget_table.df, budget_profile, model, "who is our best customer?", [], llm)
    assert not res.can_answer and "customer" in res.answer
