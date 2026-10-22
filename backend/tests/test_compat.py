from __future__ import annotations

from app.engine.compat import check_compatibility
from app.engine.profile import profile_table
from app.spec.models import ChartConfig, DashboardSpec, Metric, SemanticModel, Step, Widget


def _spec(profile):
    return DashboardSpec(source_fingerprint=profile.fingerprint(), semantic_model=SemanticModel(), widgets=[
        Widget(title="By cat", steps=[Step(op="aggregate", group_by=["expense_category"], metrics=[Metric(column="actual_usd", name="a")])],
               chart=ChartConfig(type="bar", x="expense_category", y=["a"]))])


def test_same_file_is_compatible(budget_table, budget_profile):
    rep = check_compatibility(_spec(budget_profile), budget_profile)
    assert rep.ok and not rep.messages


def test_missing_and_type_changes(budget_table, budget_profile):
    df2 = budget_table.df.drop(columns=["period_status"]).rename(columns={"expense_category": "Expense Category"})
    df2["actual_usd"] = df2["actual_usd"].astype(str)
    types = dict(budget_table.types)
    types.pop("period_status")
    types["Expense Category"] = types.pop("expense_category")
    types["actual_usd"] = "text"
    rep = check_compatibility(_spec(budget_profile), profile_table(df2, types, "b2.csv"))
    assert not rep.ok
    assert rep.missing_other == ["period_status"] and rep.missing_required == []  # renamed col matched loosely
    assert any(t.column == "actual_usd" and t.breaking for t in rep.type_changes)
    assert rep.affected_widgets == ["by_cat"]
